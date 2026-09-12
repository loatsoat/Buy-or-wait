"""Extracts a single monetary amount from a financial document image.

Images are payslips, rent receipts, utility bills, and purchase receipts
linked to a financial event whose `amount` column is blank in
financial_events.csv. We never treat a blank amount as zero (per the
problem statement), so this module is the only source of truth for
those ~16 rows.

Strategy
--------
1. OCR the image with pytesseract's word-level output and re-group
   words into visual rows using their pixel `top` coordinate (far more
   reliable than `image_to_string`'s own line breaks on multi-column
   tabular documents).
2. Look for a small set of high-confidence signals, in priority order:
     a. an explicit narrative statement ("... sum of Rupees 2,00,000")
     b. "net pay" / "transferred to" (payslips)
     c. "total amount received" (a settled receipt's final total)
     d. "total amount (to be) paid/payable/due"
     e. a bare "total" row (not "subtotal")
     f. "net amount"
     g. a label-less numeric row directly following a subtotal/tax row
        (receipts where the "Total" label itself failed to OCR)
     h. "cash paid"
   The first rule that matches wins.
3. If nothing matches, fall back to the largest plausible monetary
   amount on the page, ignoring obvious non-amounts (phone numbers,
   dates, IDs, barcodes, percentages).

An optional higher-accuracy path calls the Anthropic vision API when
ANTHROPIC_API_KEY is set in the environment; the local heuristic above
is always available and used when no key is present or the API call
fails, so the pipeline never hard-depends on network access.
"""
import os
import re

try:
    import pytesseract
    from PIL import Image
except ImportError:  # pragma: no cover
    pytesseract = None
    Image = None

# Order matters: try the most specific formats first so greedy matching
# doesn't split a single amount into pieces.
#   - Indian grouping with decimals: 1,80,000.00
#   - Western grouping with decimals: 1,839,000.00
#   - comma used as decimal separator: 41272,00
#   - Indian/Western grouping, no decimals: 1,80,000 / 1,839,000
#   - plain decimal: 4780.5
#   - plain integer: 4780
_NUM_RE = re.compile(
    r"(\d{1,3}(?:,\d{2,3})+\.\d+"
    r"|\d+,\d{2}(?!\d)"
    r"|\d{1,3}(?:,\d{2,3})+"
    r"|\d+\.\d+"
    r"|\d+)"
)

_NEGATIVE_LINE_HINTS = (
    "phone", "tax ref", "employee no", "receipt no", "account no", "acc no",
    "pan no", "license", "dispatch", "vehicle", "trip:", "gstin", "txn ref",
    "case ref", "order ref", "payroll ref", "ref no", "bill no", "biller",
    "date", "invoice no", "transaction id", "hsn", "sac", "cin", "fssai",
    "pnr", "flight no", "connector type", "charge point", "employee",
)

# Regex is searched against the lower-cased row text; the amount used is
# the LAST number found on that row. Tried in order -- the first row that
# matches any of these keyword patterns wins.
# Patterns whose value is virtually always printed on the same visual row
# as the label -- use that row's own (last) number.
_ROW_RELIABLE_PATTERNS = [
    re.compile(r"net pay", re.I),
    re.compile(r"transferred to", re.I),
    re.compile(r"total amount received", re.I),
    re.compile(r"total amount (?:to be )?(?:paid|payable|due|receiv)", re.I),
    re.compile(r"net amount", re.I),
]
# Patterns from wide/multi-column tables where the value commonly lands on
# a neighbouring OCR row instead of the label's own row -- search a small
# window and take the largest figure found.
_WINDOW_PATTERNS = [
    re.compile(r"grand total", re.I),
    re.compile(r"(?<!sub)(?<!sub )\btotal\b", re.I),
]
_NARRATIVE_PATTERN = re.compile(r"sum of (?:rupees|rs\.?|inr|usd|eur|zar|idr)?\s*([\d.,]+)", re.I)
_CASH_PAID_PATTERN = re.compile(r"cash paid", re.I)


def _clean_amount(token):
    if re.fullmatch(r"\d+,\d{2}", token):
        # comma used as a decimal separator, e.g. "41272,00"
        token = token.replace(",", ".")
    else:
        token = token.replace(",", "")
    try:
        return float(token)
    except ValueError:
        return None


def _looks_like_amount_token(tok, full_line):
    """Filter out obvious non-monetary numbers (phone numbers, long IDs,
    barcodes, bare years, percentages)."""
    digits_only = tok.replace(",", "").replace(".", "")
    if len(digits_only) >= 7 and "." not in tok and "," not in tok:
        return False  # looks like an ID / phone / account / HSN / barcode number
    # percentage right after the number, e.g. "12%"
    idx = full_line.find(tok)
    tail = full_line[idx + len(tok): idx + len(tok) + 1]
    if tail == "%":
        return False
    return True


def _rows_from_image(image_path):
    """Group OCR words into visual rows using a tolerance-based clustering
    on the `top` coordinate rather than a fixed bucket size, since a fixed
    bucket boundary can split two words on the same printed line into
    different rows when their tops differ by only a few pixels."""
    img = Image.open(image_path)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    n = len(data["text"])
    words = []
    for i in range(n):
        text = data["text"][i].strip()
        if not text:
            continue
        words.append((data["top"][i], data["left"][i], data["height"][i], text))
    words.sort(key=lambda w: (w[0], w[1]))

    rows_raw = []  # list of [avg_top, [(left, text), ...]]
    for top, left, height, text in words:
        tolerance = max(8, int(height * 0.6))
        placed = False
        for row in rows_raw:
            if abs(top - row[0]) <= tolerance:
                row[1].append((left, text))
                cnt = len(row[1])
                row[0] = (row[0] * (cnt - 1) + top) / cnt
                placed = True
                break
        if not placed:
            rows_raw.append([top, [(left, text)]])

    rows_raw.sort(key=lambda r: r[0])
    rows = []
    for _, items in rows_raw:
        items.sort()
        rows.append(" ".join(t for _, t in items))
    return rows


def _numbers_in_line(line):
    out = []
    for m in _NUM_RE.finditer(line):
        tok = m.group(1)
        if _looks_like_amount_token(tok, line):
            val = _clean_amount(tok)
            if val is not None:
                out.append(val)
    return out


def _extract_local_heuristic(image_path):
    rows = _rows_from_image(image_path)
    lower_rows = [r.lower() for r in rows]

    # a) narrative "sum of Rupees X" pattern (highest confidence: explicit statement)
    for row in rows:
        m = _NARRATIVE_PATTERN.search(row.lower())
        if m:
            val = _clean_amount(m.group(1))
            if val is not None:
                return val

    def window_numbers(idx, before=1, after=2):
        """Numbers found within a small row window around idx. Scanned
        tabular documents often OCR a label and its value onto slightly
        different visual rows, so we look at neighbours too and take the
        largest figure -- the total is virtually always the biggest
        number printed near its own label."""
        out = []
        for j in range(max(0, idx - before), min(len(rows), idx + after + 1)):
            if any(h in lower_rows[j] for h in _NEGATIVE_LINE_HINTS):
                continue
            out.extend(_numbers_in_line(rows[j]))
        return out

    # b) row-reliable keyword patterns: trust the matched row's own value
    for pattern in _ROW_RELIABLE_PATTERNS:
        for idx, lower in enumerate(lower_rows):
            if any(h in lower for h in _NEGATIVE_LINE_HINTS):
                continue
            if pattern.search(lower):
                nums = _numbers_in_line(rows[idx])
                if nums:
                    return nums[-1]

    # b2) window-based patterns (grand total / bare total): these commonly
    # land on a neighbouring row in wide multi-column tables
    for pattern in _WINDOW_PATTERNS:
        for idx, lower in enumerate(lower_rows):
            if any(h in lower for h in _NEGATIVE_LINE_HINTS):
                continue
            if pattern.search(lower):
                nums = window_numbers(idx)
                if nums:
                    return max(nums)

    # c) label-less numeric row right after a subtotal/tax row -- handles
    # receipts where the "Total" label itself failed to OCR
    for idx, lower in enumerate(lower_rows):
        if "subtotal" in lower or lower.strip().startswith("tax"):
            for j in (idx + 1, idx + 2):
                if j < len(rows):
                    nums = _numbers_in_line(rows[j])
                    has_label = any(c.isalpha() for c in rows[j])
                    if nums and not has_label:
                        return nums[-1]

    # d) "cash paid" -- lower priority than the above since change may be
    # included (cash paid can exceed the actual amount owed)
    for idx, lower in enumerate(lower_rows):
        if any(h in lower for h in _NEGATIVE_LINE_HINTS):
            continue
        if _CASH_PAID_PATTERN.search(lower):
            nums = _numbers_in_line(rows[idx])
            if nums:
                return nums[-1]

    # e) fallback: largest plausible amount on the page
    all_nums = []
    for row, lower in zip(rows, lower_rows):
        if any(h in lower for h in _NEGATIVE_LINE_HINTS):
            continue
        all_nums.extend(_numbers_in_line(row))
    if all_nums:
        return max(all_nums)
    return None


def _extract_via_anthropic(image_path, api_key):  # pragma: no cover - optional path
    import base64
    import json
    import urllib.request

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {
                        "type": "text",
                        "text": (
                            "This is a financial document (payslip, receipt, or bill) linked to a "
                            "financial ledger event with a blank amount. Reply with ONLY the single "
                            "final monetary amount that this document confirms (net pay for a "
                            "payslip, the total amount due/paid for a receipt or bill). No currency "
                            "symbol, no commas, no words -- just the plain number."
                        ),
                    },
                ],
            }
        ],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = "".join(b.get("text", "") for b in data.get("content", []))
    m = re.search(r"[\d,]+\.?\d*", text)
    if not m:
        return None
    return _clean_amount(m.group(0))


def extract_amount(image_path):
    """Best-effort extraction of the single relevant amount from a document
    image. Returns a float, or None if nothing could be extracted."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            val = _extract_via_anthropic(image_path, api_key)
            if val is not None:
                return val
        except Exception:
            pass  # fall through to local heuristic
    if pytesseract is None or Image is None:
        return None
    if not os.path.exists(image_path):
        return None
    try:
        return _extract_local_heuristic(image_path)
    except Exception:
        return None

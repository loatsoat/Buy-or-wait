#!/usr/bin/env python3
"""Evaluate the pipeline.

1. Accuracy: re-run the pipeline on the 25 labelled rows of sample_requests.csv and
   compare each field with its label, pairing prediction and label by request_id.
2. Validity: check dataset/output.csv against the fixed output rules, row by row
   against requests.csv in its original order.

Usage: python code/evaluation/main.py     (exit 1 if any output rule is broken)
"""
import csv
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import config
from lib.currency import CurrencyConverter
from lib.data_loader import Dataset
from lib.evidence import resolve_blank_amounts
from lib.validate import output_problems
from main import run_requests

LABEL_FIELDS = config.OUTPUT_COLUMNS[1:]
AMOUNT_TOLERANCE = 0.01  # relative; amounts within 1% of the label count as correct


def _plan(text):
    if text == 'none':
        return []
    return [(date.fromisoformat(d), float(a)) for d, a in (p.split(':') for p in text.split('|'))]


def _close(a, b):
    return abs(a - b) <= AMOUNT_TOLERANCE * max(abs(b), 1.0)


def field_matches(field, predicted, label):
    if field == 'amount_safe_to_pay':
        return _close(float(predicted), float(label))
    if field == 'payment_plan':
        p, t = _plan(predicted), _plan(label)
        return len(p) == len(t) and all(pd == td and _close(pa, ta) for (pd, pa), (td, ta) in zip(p, t))
    if field == 'decision_explanation':
        return bool(predicted.strip())  # wording is graded by the organisers; here only presence
    return predicted == label


def main():
    dataset = Dataset()
    converter = CurrencyConverter(dataset.rates_raw)
    unresolved, _ = resolve_blank_amounts(dataset)

    print('LABELLED SAMPLE (dataset/sample_requests.csv)')
    rows, held = run_requests(dataset.sample_requests, dataset, converter, unresolved)
    correct = dict.fromkeys(LABEL_FIELDS, 0)
    for row, label in zip(rows, dataset.sample_requests):
        assert row['request_id'] == label['request_id']
        wrong = [f for f in LABEL_FIELDS if not field_matches(f, row[f], label[f])]
        for f in LABEL_FIELDS:
            correct[f] += f not in wrong
        print(f"  {'ok  ' if not wrong else 'FAIL'} {row['request_id']}")
        for f in wrong:
            if f != 'decision_explanation':
                print(f'         {f}: predicted {row[f]!r}, label {label[f]!r}')
    total = len(rows)
    print()
    for f in LABEL_FIELDS:
        print(f'  {f:<32} {correct[f]:2d}/{total}  {correct[f] / total:.0%}')
    print(f'  answered conservatively for lack of evidence: {len(held)}')

    print('\nOUTPUT RULES (dataset/output.csv against dataset/requests.csv)')
    with open(os.path.join(config.DATASET_DIR, 'output.csv'), newline='', encoding='utf-8') as f:
        output_rows = list(csv.DictReader(f))
    problems = output_problems(output_rows, dataset.requests, dataset)
    print(f'  {len(output_rows)} rows checked, {len(problems)} problems')
    for problem in problems[:30]:
        print(f'  {problem}')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())

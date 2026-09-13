"""Currency conversion using exchange_rates.csv with BFS path finding."""
from collections import defaultdict, deque
from datetime import date


class CurrencyConverter:
    def __init__(self, rates_raw):
        self.rates_by_date = defaultdict(list)
        for r in rates_raw:
            d = r["rate_date"]
            self.rates_by_date[d].append((r["from_currency"], r["to_currency"], float(r["rate"])))
        self.all_dates = sorted(self.rates_by_date.keys())
        self._graph_cache = {}

    def _closest_date(self, target_date_str):
        if target_date_str in self.rates_by_date:
            return target_date_str
        prior = [d for d in self.all_dates if d <= target_date_str]
        if prior:
            return prior[-1]
        return min(self.all_dates, key=lambda d: abs(_date_diff(d, target_date_str)))

    def _graph(self, date_key):
        if date_key in self._graph_cache:
            return self._graph_cache[date_key]
        graph = defaultdict(dict)
        for a, b, rate in self.rates_by_date[date_key]:
            graph[a][b] = rate
            if rate != 0:
                graph[b].setdefault(a, 1.0 / rate)
        self._graph_cache[date_key] = graph
        return graph

    def convert(self, amount, from_currency, to_currency, on_date_str):
        if amount is None:
            return None
        if from_currency == to_currency:
            return amount
        date_key = self._closest_date(on_date_str)
        graph = self._graph(date_key)
        rate = self._bfs_rate(graph, from_currency, to_currency)
        if rate is None:
            raise ValueError(f"No FX path from {from_currency} to {to_currency} near {on_date_str}")
        return amount * rate

    @staticmethod
    def _bfs_rate(graph, src, dst):
        if src == dst:
            return 1.0
        seen = {src}
        q = deque([(src, 1.0)])
        while q:
            node, acc = q.popleft()
            for neigh, rate in graph.get(node, {}).items():
                if neigh in seen:
                    continue
                new_acc = acc * rate
                if neigh == dst:
                    return new_acc
                seen.add(neigh)
                q.append((neigh, new_acc))
        return None


def _date_diff(d1, d2):
    def p(s):
        y, m, day = s.split("-")
        return date(int(y), int(m), int(day))

    return (p(d1) - p(d2)).days

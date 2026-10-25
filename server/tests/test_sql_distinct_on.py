"""Every DISTINCT ON key list must prefix its ORDER BY (Postgres requirement)."""

import pathlib
import re
import unittest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
DISTINCT = re.compile(r"DISTINCT ON \(([^)]*)\)(.*?)ORDER BY\s+(.*?)(?:\n\s*\n|\)|LIMIT|$)", re.S)


def _keys(text: str) -> list[str]:
    return [k.strip().split()[0] for k in text.split(",") if k.strip()]


class DistinctOnOrderTest(unittest.TestCase):
    def test_distinct_on_keys_prefix_order_by(self):
        checked = 0
        for path in APP.rglob("*.py"):
            source = path.read_text()
            for match in DISTINCT.finditer(source):
                keys = _keys(match.group(1))
                order = _keys(match.group(3))[: len(keys)]
                self.assertEqual(order, keys, f"{path.name}: DISTINCT ON {keys} vs ORDER BY {order}")
                checked += 1
        self.assertGreater(checked, 0)

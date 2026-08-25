import unittest
from pathlib import Path
from app.services.instrument_importer import (
    find_nifty500_csv_path,
    read_nifty500_csv,
    DEFAULT_CSV_PATH,
)


class InstrumentImporterTests(unittest.TestCase):
    def test_find_nifty500_csv_path(self):
        csv_path = find_nifty500_csv_path()
        self.assertIsNotNone(csv_path)
        self.assertTrue(csv_path.is_file())

    def test_read_nifty500_csv_parses_symbols(self):
        csv_path = find_nifty500_csv_path()
        self.assertIsNotNone(csv_path)
        rows = read_nifty500_csv(csv_path)
        self.assertEqual(len(rows), 500)
        symbols = [r.symbol for r in rows]
        self.assertIn("RELIANCE", symbols)
        self.assertIn("TCS", symbols)
        self.assertIn("INFY", symbols)
        self.assertTrue(all(r.fyers_symbol.startswith("NSE:") for r in rows))


if __name__ == "__main__":
    unittest.main()

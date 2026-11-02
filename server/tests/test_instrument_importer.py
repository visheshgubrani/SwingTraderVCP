import tempfile
import unittest
from pathlib import Path

from app.services.instrument_importer import (
    find_nifty500_csv_path,
    read_nifty500_csv,
)

HEADER = "Company Name,Industry,Symbol,Series,ISIN Code\n"


class InstrumentImporterTests(unittest.TestCase):
    def test_find_nifty500_csv_path(self):
        csv_path = find_nifty500_csv_path()
        self.assertIsNotNone(csv_path)
        self.assertTrue(csv_path.is_file())

    def test_read_nifty500_csv_parses_symbols(self):
        csv_path = find_nifty500_csv_path()
        self.assertIsNotNone(csv_path)
        rows = read_nifty500_csv(csv_path)
        # NSE's list carries REIT units and corporate-action placeholders;
        # only equity shares with real ISINs remain.
        self.assertGreaterEqual(len(rows), 490)
        self.assertLessEqual(len(rows), 500)
        symbols = [r.symbol for r in rows]
        self.assertIn("RELIANCE", symbols)
        self.assertIn("TCS", symbols)
        self.assertIn("INFY", symbols)
        self.assertTrue(all(r.fyers_symbol.startswith("NSE:") for r in rows))
        self.assertTrue(all(r.series in {"EQ", "BE"} for r in rows))
        self.assertTrue(all(r.isin.startswith("IN") for r in rows))

    def test_skips_reits_and_placeholders_keeps_trade_for_trade(self):
        body = (
            "Reliance Industries Ltd.,Oil Gas,RELIANCE,EQ,INE002A01018\n"
            "HFCL Ltd.,Telecommunication,HFCL,BE,INE548A01028\n"
            "EMBASSY OFFICE PARKS REIT,Realty,EMBASSY,RR,INE041025011\n"
            "Dummy HEG Ltd.,Capital Goods,DUMMYHEG,EQ,DUM545A01024\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.csv"
            path.write_text(HEADER + body, encoding="utf-8")
            rows = read_nifty500_csv(path)
        self.assertEqual([r.symbol for r in rows], ["RELIANCE", "HFCL"])
        self.assertEqual(rows[1].fyers_symbol, "NSE:HFCL-BE")


if __name__ == "__main__":
    unittest.main()

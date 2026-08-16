import unittest
from decimal import Decimal

from app.services.fyers_broker_reads import (
    FyersBrokerReadError,
    parse_available_funds,
)


class BrokerFundsParsingTests(unittest.TestCase):
    def test_reads_explicit_available_balance(self):
        payload = {
            "s": "ok",
            "fund_limit": [
                {"id": 1, "title": "Total Balance", "equityAmount": 99999},
                {"id": 10, "title": "Available Balance", "equityAmount": "12345.67"},
            ],
        }
        self.assertEqual(parse_available_funds(payload), Decimal("12345.67"))

    def test_does_not_infer_availability_from_total_balance(self):
        with self.assertRaises(FyersBrokerReadError):
            parse_available_funds(
                {"s": "ok", "fund_limit": [{"id": 1, "title": "Total Balance", "equityAmount": 99999}]}
            )


if __name__ == "__main__":
    unittest.main()

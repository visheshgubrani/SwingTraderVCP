import unittest
from app.domain.p10_geometry import CandleData
from app.services.proposal_renderer import render_proposal_charts


class TestProposalRenderer(unittest.TestCase):
    def test_render_proposal_charts(self):
        # Generate 260 sample candles
        candles = []
        price = 100.0
        for i in range(260):
            price += 0.5 if i % 2 == 0 else -0.3
            month = (i // 28) % 12 + 1
            day = (i % 28) + 1
            candles.append(CandleData(
                open=price,
                high=price + 2.0,
                low=price - 1.5,
                close=price + 0.5,
                volume=100000 + i * 500,
                date=f"2024-{month:02d}-{day:02d}",
            ))

        charts = render_proposal_charts(
            candles=candles,
            symbol="TESTSTOCK",
            pivot_price=220.0,
            stop_price=210.0,
        )

        self.assertEqual(charts.renderer_version, "p10_mplfinance_v3")
        self.assertTrue(charts.context_png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(charts.detail_png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(len(charts.context_hash), 64)
        self.assertEqual(len(charts.detail_hash), 64)
        repeated = render_proposal_charts(
            candles=candles,
            symbol="TESTSTOCK",
            pivot_price=220.0,
            stop_price=210.0,
        )
        self.assertEqual(repeated.context_hash, charts.context_hash)
        self.assertEqual(repeated.detail_hash, charts.detail_hash)
        without_stop = render_proposal_charts(
            candles=candles,
            symbol="TESTSTOCK",
            pivot_price=220.0,
            stop_price=999.0,
        )
        self.assertEqual(without_stop.detail_hash, charts.detail_hash)


if __name__ == "__main__":
    unittest.main()

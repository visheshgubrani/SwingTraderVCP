import unittest

from app.services.screening_diagnostics import build_scanner_diagnostics


def row(rank: int, symbol: str, industry: str, rs: float, rs_line: float, high: float):
    components = {
        "relative_strength": {"points": rs, "max_points": 15, "raw_value": rs},
        "rs_line_high": {"points": rs_line, "max_points": 10, "raw_value": rs_line},
        "high_proximity": {"points": high, "max_points": 0, "raw_value": high},
        "stage2": {
            "points": 10,
            "max_points": 10,
            "raw_value": {"core_checks_passed": 5},
        },
    }
    return {
        "result_rank": rank,
        "technical_score": rs + rs_line + 10,
        "pct_from_52w_high": high / 100,
        "symbol": symbol,
        "technical_metrics": {
            "rs_rating": int(rs),
            "industry_key": industry,
            "fundamental_selected": rank <= 2,
            "score": {"components": components},
        },
    }


class ScreeningDiagnosticsTests(unittest.TestCase):
    def test_reports_correlations_saturation_leave_out_and_concentration(self) -> None:
        rows = [
            row(1, "AAA", "it", 15, 10, 5),
            row(2, "BBB", "it", 12, 8, 10),
            row(3, "CCC", "industrial", 9, 6, 15),
        ]
        diagnostics = build_scanner_diagnostics(
            rows,
            xbrl_counts={"total": 4, "ambiguous": 1, "unknown_taxonomy": 0, "missing": 2},
        )

        self.assertIn(
            "relative_strength__rs_line_high",
            diagnostics["component_correlations"],
        )
        self.assertEqual(
            diagnostics["component_statistics"]["stage2"]["saturation_pct"],
            100.0,
        )
        self.assertIn(
            "relative_strength",
            diagnostics["leave_one_component_out"],
        )
        self.assertEqual(
            diagnostics["industry_concentration"]["fundamental_selection"],
            {"it": 2},
        )
        self.assertNotIn("version_comparison", diagnostics)
        self.assertEqual(diagnostics["xbrl_coverage"]["ambiguity_pct"], 25.0)


if __name__ == "__main__":
    unittest.main()

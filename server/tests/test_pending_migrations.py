import unittest
from pathlib import Path

from app.services.pending_migrations import (
    BASELINE_THROUGH,
    DEFAULT_MIGRATIONS_DIR,
    baseline_filenames,
    numeric_prefix,
    pending_filenames,
    sorted_migration_names,
)


class PendingMigrationSelectionTests(unittest.TestCase):
    def test_numeric_prefix_and_sort_order(self) -> None:
        names = [
            "025_p10_proposal_prompt_v5.sql",
            "9_not_padded.sql",
            "024_p10_proposal_schema_v4.sql",
            "001_p3_paper_trading.sql",
        ]
        self.assertEqual(numeric_prefix(names[0]), 25)
        self.assertEqual(
            sorted_migration_names(names),
            [
                "001_p3_paper_trading.sql",
                "9_not_padded.sql",
                "024_p10_proposal_schema_v4.sql",
                "025_p10_proposal_prompt_v5.sql",
            ],
        )

    def test_baseline_stops_at_024(self) -> None:
        names = [
            "023_p10_two_leg_staged_and_operator_adjust.sql",
            "024_p10_proposal_schema_v4.sql",
            "025_p10_proposal_prompt_v5.sql",
        ]
        self.assertEqual(BASELINE_THROUGH, 24)
        self.assertEqual(
            baseline_filenames(names),
            [
                "023_p10_two_leg_staged_and_operator_adjust.sql",
                "024_p10_proposal_schema_v4.sql",
            ],
        )

    def test_pending_skips_already_applied(self) -> None:
        names = [
            "024_p10_proposal_schema_v4.sql",
            "025_p10_proposal_prompt_v5.sql",
        ]
        self.assertEqual(
            pending_filenames(names, {"024_p10_proposal_schema_v4.sql"}),
            ["025_p10_proposal_prompt_v5.sql"],
        )

    def test_repo_migrations_include_v5_and_are_numbered(self) -> None:
        files = sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql"))
        self.assertTrue(files)
        self.assertTrue(
            (DEFAULT_MIGRATIONS_DIR / "025_p10_proposal_prompt_v5.sql").is_file()
        )
        for path in files:
            self.assertIsNotNone(numeric_prefix(path.name), path.name)


if __name__ == "__main__":
    unittest.main()

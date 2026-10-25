"""The diagnostic report must stay read-only."""

import importlib.util
import pathlib
import re
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "p10_disposition_report.py"


class DispositionReportReadOnlyTest(unittest.TestCase):
    def test_every_section_is_a_select_without_dml_or_ddl(self):
        spec = importlib.util.spec_from_file_location("p10_disposition_report", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(module.SECTIONS)
        for title, sql in module.SECTIONS.items():
            self.assertRegex(sql.strip(), r"^(WITH|SELECT)\b", title)
            self.assertIsNone(
                re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE|COPY|GRANT)\b", sql, re.I),
                title,
            )
            self.assertNotIn("_image", sql, title)  # never pull chart image columns

import re
import unittest
from pathlib import Path


LOCK_PATH = Path(__file__).resolve().parents[1] / "uv.lock"


def _locked_version(package_name: str) -> tuple[int, ...]:
    pattern = rf'(?ms)^\[\[package\]\]\nname = "{re.escape(package_name)}"\nversion = "([^"]+)"'
    match = re.search(pattern, LOCK_PATH.read_text())
    if match is None:
        raise AssertionError(f"{package_name} missing from uv.lock")
    return tuple(int(part) for part in match.group(1).split(".")[:3])


class FyersTransitiveOverrideTests(unittest.TestCase):
    def test_vulnerable_fyers_transitives_are_overridden(self) -> None:
        self.assertGreaterEqual(_locked_version("aiohttp"), (3, 11, 18))
        self.assertGreaterEqual(_locked_version("requests"), (2, 32, 4))
        self.assertGreaterEqual(_locked_version("setuptools"), (78, 1, 1))

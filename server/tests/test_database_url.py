"""Tests for production database URL assembly (password encoding)."""

from __future__ import annotations

import unittest

from sqlalchemy.engine.url import make_url

from app.config import Settings, build_asyncpg_database_url


class DatabaseUrlAssemblyTests(unittest.TestCase):
    def test_password_with_at_sign_keeps_postgres_host(self) -> None:
        url = build_asyncpg_database_url(
            user="algo",
            password="MyP@ssw0rd",
            host="postgres",
            port=5432,
            database="algo_trading",
        )
        parsed = make_url(url)
        self.assertEqual(parsed.host, "postgres")
        self.assertEqual(parsed.password, "MyP@ssw0rd")
        self.assertEqual(parsed.username, "algo")
        self.assertEqual(parsed.database, "algo_trading")

    def test_settings_rebuilds_url_when_postgres_host_set(self) -> None:
        settings = Settings(
            postgres_host="postgres",
            postgres_user="algo",
            postgres_password="MyP@ssw0rd",
            postgres_db="algo_trading",
            postgres_port=5432,
            # Intentionally wrong — must be ignored when POSTGRES_HOST is set.
            database_url="postgresql+asyncpg://algo:x@wrong-host:5432/algo_trading",
        )
        parsed = make_url(settings.database_url)
        self.assertEqual(parsed.host, "postgres")
        self.assertEqual(parsed.password, "MyP@ssw0rd")


if __name__ == "__main__":
    unittest.main()

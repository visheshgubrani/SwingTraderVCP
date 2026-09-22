"""RFC 6238 TOTP tests (secret handling + published test vectors)."""

import base64
import datetime
import unittest
from zoneinfo import ZoneInfo

from app.domain.totp import (
    TotpSecretError,
    decode_secret,
    generate_totp,
    hotp,
    normalize_secret,
    seconds_remaining,
)

# RFC 6238 Appendix B reference secret ("12345678901234567890") in base32.
RFC_SECRET_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_SECRET_ASCII = "12345678901234567890"


class TotpVectorTests(unittest.TestCase):
    def test_rfc6238_sha1_vectors_with_8_digits(self):
        vectors = [
            (59, "94287082"),
            (1111111109, "07081804"),
            (1111111111, "14050471"),
            (1234567890, "89005924"),
            (2000000000, "69279037"),
            (20000000000, "65353130"),
        ]
        for timestamp, expected in vectors:
            with self.subTest(timestamp=timestamp):
                self.assertEqual(
                    generate_totp(RFC_SECRET_B32, at=float(timestamp), digits=8),
                    expected,
                )

    def test_six_digit_truncation_matches_last_six_of_rfc_vector(self):
        self.assertEqual(generate_totp(RFC_SECRET_B32, at=59.0), "287082")
        self.assertEqual(generate_totp(RFC_SECRET_B32, at=1111111109.0), "081804")

    def test_ascii_encoded_secret_matches_base32_form(self):
        ascii_b32 = base64.b32encode(RFC_SECRET_ASCII.encode("ascii")).decode("ascii")
        self.assertEqual(generate_totp(ascii_b32, at=59.0, digits=8), "94287082")
        self.assertEqual(
            generate_totp(RFC_SECRET_B32, at=59.0, digits=8),
            generate_totp(ascii_b32, at=59.0, digits=8),
        )

    def test_counter_math_is_step_based(self):
        # Same 30s step → identical code; next step → different counter.
        self.assertEqual(hotp(RFC_SECRET_B32, 1), hotp(RFC_SECRET_B32, 1))
        self.assertNotEqual(
            generate_totp(RFC_SECRET_B32, at=30.0),
            generate_totp(RFC_SECRET_B32, at=60.0),
        )


class TotpSecretTests(unittest.TestCase):
    def test_normalize_accepts_grouped_lowercase_and_padding(self):
        grouped = "gezd gnbv gy3t qojq gezd gnbv gy3t qojq"
        self.assertEqual(normalize_secret(grouped), RFC_SECRET_B32)
        self.assertEqual(normalize_secret(f"{RFC_SECRET_B32[:4]}-{RFC_SECRET_B32[4:]}"), RFC_SECRET_B32)

    def test_missing_secret_is_rejected(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(TotpSecretError):
                    decode_secret(value)

    def test_invalid_base32_is_rejected(self):
        with self.assertRaises(TotpSecretError):
            decode_secret("!!!!not-base32!!!!")

    def test_secret_never_needs_to_be_printed(self):
        # Guards against a future accidental repr/log of the secret object.
        class Probe:
            def __init__(self, value):
                self.value = value

            def __repr__(self):
                return "Probe(<redacted>)"

        probe = Probe(RFC_SECRET_B32)
        self.assertNotIn(RFC_SECRET_B32, repr(probe))


class TotpTimingTests(unittest.TestCase):
    def test_seconds_remaining_uses_the_step_boundary(self):
        self.assertAlmostEqual(seconds_remaining(at=0.0), 30.0)
        self.assertAlmostEqual(seconds_remaining(at=29.0), 1.0)
        self.assertAlmostEqual(seconds_remaining(at=30.0), 30.0)

    def test_aware_datetime_input(self):
        moment = datetime.datetime(2026, 9, 15, 7, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
        code = generate_totp(RFC_SECRET_B32, at=moment)
        self.assertEqual(len(code), 6)
        self.assertAlmostEqual(
            seconds_remaining(at=moment), seconds_remaining(at=moment.timestamp())
        )

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            generate_totp(RFC_SECRET_B32, at=datetime.datetime(2026, 9, 15, 7, 15))


if __name__ == "__main__":
    unittest.main()

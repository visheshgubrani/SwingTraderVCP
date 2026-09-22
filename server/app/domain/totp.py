"""RFC 6238 TOTP generation for the Fyers External-2FA headless login flow.

Pure, dependency-free (stdlib ``hmac``/``hashlib``/``base64``) so the money-path
auth chain adds no third-party supply chain. Verified against the RFC 6238
Appendix B test vectors in ``tests/test_totp.py``.

The secret is a broker credential: callers must never log it, echo it in an API
response, or persist it outside the environment.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import hmac
import time

TOTP_EPOCH = 0  # RFC 6238 T0
DEFAULT_PERIOD_SECONDS = 30
DEFAULT_DIGITS = 6

_HASH_ALGORITHMS = {"sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512}


class TotpSecretError(ValueError):
    """Raised when a TOTP secret is missing or not valid base32."""


def normalize_secret(raw: str | None) -> str:
    """Normalize a user-copied base32 secret.

    Fyers shows the External-2FA secret in the usual grouped form
    (``abcd efgh ijkl mnop``) and users paste it with spaces, dashes, or lower
    case. Padding is re-added so ``base64`` decoding succeeds.
    """
    if not raw:
        raise TotpSecretError("TOTP secret is not configured")
    cleaned = "".join(
        char for char in raw.strip().upper() if char not in " -_:\t\r\n"
    )
    if not cleaned:
        raise TotpSecretError("TOTP secret is empty after normalization")
    missing_padding = len(cleaned) % 8
    if missing_padding:
        cleaned = cleaned + "=" * (8 - missing_padding)
    return cleaned


def decode_secret(secret: str | None) -> bytes:
    """Decode a base32 TOTP secret into raw key bytes."""
    normalized = normalize_secret(secret)
    try:
        return base64.b32decode(normalized, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise TotpSecretError("TOTP secret is not valid base32") from exc


def _counter_for(at: float, period: int) -> int:
    if period <= 0:
        raise ValueError("TOTP period must be positive")
    return int((at - TOTP_EPOCH) // period)


def hotp(
    secret: str | None,
    counter: int,
    *,
    digits: int = DEFAULT_DIGITS,
    algorithm: str = "sha1",
) -> str:
    """RFC 4226 HOTP value for an explicit counter (used by RFC 6238 TOTP)."""
    digest_mod = _HASH_ALGORITHMS.get(algorithm.lower())
    if digest_mod is None:
        raise ValueError(f"Unsupported TOTP algorithm: {algorithm}")
    key = decode_secret(secret)
    message = counter.to_bytes(8, byteorder="big")
    digest = hmac.new(key, message, digest_mod).digest()
    offset = digest[-1] & 0x0F
    binary = (
        (digest[offset] & 0x7F) << 24
        | (digest[offset + 1] & 0xFF) << 16
        | (digest[offset + 2] & 0xFF) << 8
        | (digest[offset + 3] & 0xFF)
    )
    return str(binary % (10**digits)).zfill(digits)


def generate_totp(
    secret: str | None,
    *,
    at: datetime.datetime | float | None = None,
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD_SECONDS,
    algorithm: str = "sha1",
) -> str:
    """Current TOTP code for the configured secret."""
    timestamp = _as_epoch(at)
    return hotp(
        secret,
        _counter_for(timestamp, period),
        digits=digits,
        algorithm=algorithm,
    )


def seconds_remaining(
    *,
    at: datetime.datetime | float | None = None,
    period: int = DEFAULT_PERIOD_SECONDS,
) -> float:
    """Seconds until the current TOTP step rolls over.

    A code generated in the last couple of seconds of a step is routinely
    rejected by the broker because it expires in transit; callers use this to
    wait for a fresh step instead of burning a login attempt.
    """
    timestamp = _as_epoch(at)
    if period <= 0:
        raise ValueError("TOTP period must be positive")
    return period - (timestamp % period)


def _as_epoch(at: datetime.datetime | float | None) -> float:
    if at is None:
        return time.time()
    if isinstance(at, datetime.datetime):
        if at.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return at.timestamp()
    return float(at)

import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.config import settings

async def save_fyers_token(
    session: AsyncSession,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime.datetime
) -> None:
    """
    Saves the Fyers access token and refresh token encrypted in the broker_auth_tokens table.
    The encryption is performed database-side via pgcrypto's pgp_sym_encrypt function,
    keyed by settings.token_encryption_passphrase (TOKEN_ENCRYPTION_KEY).
    """
    query = text("""
        INSERT INTO broker_auth_tokens (
            broker,
            token_scope,
            access_token_encrypted,
            refresh_token_encrypted,
            expires_at,
            refreshed_at,
            updated_at
        )
        VALUES (
            'fyers',
            'default',
            encode(pgp_sym_encrypt(CAST(:access_token AS text), :key), 'base64'),
            CASE WHEN CAST(:refresh_token AS text) IS NOT NULL THEN encode(pgp_sym_encrypt(CAST(:refresh_token AS text), :key), 'base64') ELSE NULL END,
            :expires_at,
            now(),
            now()
        )
        ON CONFLICT (broker, token_scope) DO UPDATE SET
            access_token_encrypted = EXCLUDED.access_token_encrypted,
            refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
            expires_at = EXCLUDED.expires_at,
            refreshed_at = now(),
            updated_at = now()
    """)
    await session.execute(query, {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "key": settings.token_encryption_passphrase,
        "expires_at": expires_at
    })

async def get_fyers_token(session: AsyncSession) -> dict | None:
    """
    Decrypts and returns the active Fyers token if available.
    """
    query = text("""
        SELECT 
            pgp_sym_decrypt(decode(access_token_encrypted, 'base64'), :key) as access_token,
            CASE WHEN refresh_token_encrypted IS NOT NULL 
                 THEN pgp_sym_decrypt(decode(refresh_token_encrypted, 'base64'), :key)
                 ELSE NULL 
            END as refresh_token,
            expires_at
        FROM broker_auth_tokens
        WHERE broker = 'fyers' AND token_scope = 'default'
    """)
    result = await session.execute(query, {"key": settings.token_encryption_passphrase})
    row = result.first()
    if row:
        return {
            "access_token": row.access_token,
            "refresh_token": row.refresh_token,
            "expires_at": row.expires_at
        }
    return None

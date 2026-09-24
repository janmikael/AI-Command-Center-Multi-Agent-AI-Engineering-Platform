"""Password hashing (Argon2id) and session token helpers."""

import hashlib
import hmac
import secrets
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults are Argon2id with the RFC 9106 "low memory" parameters.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@cache
def _dummy_hash() -> str:
    return _hasher.hash("timing-equalizer")


def burn_verify_time(password: str) -> None:
    """Spend the same time as a real verification, so unknown emails aren't distinguishable."""
    verify_password(_dummy_hash(), password)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_id_for(token: str, secret: str) -> str:
    """The database stores only this HMAC. A leaked sessions table can't be replayed as cookies."""
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()

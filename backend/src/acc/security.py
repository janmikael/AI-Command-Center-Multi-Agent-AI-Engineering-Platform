"""Password hashing (Argon2id), session token helpers, and per-run tokens."""

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
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


# ─── Run tokens ──────────────────────────────────────────────────────────
# A self-contained, HMAC-signed credential the launcher mints for exactly one run
# (PLAN-PHASE-2-M2.md §2.4). Not a JWT and no new dependency: one fixed algorithm and
# one secret is all a runner-to-internal-API credential needs, same minimal style as
# session_id_for above rather than session_id_for's DB-backed lookup — a run token has
# no server-side record to revoke, so everything it authorizes has to be inside it.


@dataclass(frozen=True)
class RunTokenClaims:
    run_id: uuid.UUID
    agent_id: uuid.UUID
    exp: int  # unix seconds


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def mint_run_token(*, run_id: uuid.UUID, agent_id: uuid.UUID, expires_at: int, secret: str) -> str:
    payload = json.dumps(
        {"run_id": str(run_id), "agent_id": str(agent_id), "exp": expires_at},
        separators=(",", ":"),
    ).encode()
    payload_b64 = _b64url_encode(payload)
    signature = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_run_token(token: str, secret: str, *, now: int) -> RunTokenClaims | None:
    """None on any failure — malformed, tampered payload, tampered signature, or expired.
    Deliberately doesn't distinguish which; the caller returns a uniform 401 either way."""
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        data = json.loads(_b64url_decode(payload_b64))
        claims = RunTokenClaims(
            run_id=uuid.UUID(data["run_id"]),
            agent_id=uuid.UUID(data["agent_id"]),
            exp=int(data["exp"]),
        )
    except (ValueError, KeyError, TypeError):
        return None
    if claims.exp < now:
        return None
    return claims

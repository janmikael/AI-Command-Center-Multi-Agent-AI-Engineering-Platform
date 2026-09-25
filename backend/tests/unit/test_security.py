import uuid

from acc.security import (
    hash_password,
    mint_run_token,
    needs_rehash,
    new_session_token,
    session_id_for,
    verify_password,
    verify_run_token,
)


def test_hash_is_argon2id_and_verifies() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery staple")
    assert not verify_password(hashed, "wrong password")
    assert not needs_rehash(hashed)


def test_verify_tolerates_garbage_hash() -> None:
    assert not verify_password("not-a-hash", "anything")


def test_session_tokens_are_unique_and_long() -> None:
    tokens = {new_session_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 43 for t in tokens)  # 32 random bytes, base64url


def test_session_id_is_keyed_hmac() -> None:
    token = "some-token"
    assert session_id_for(token, "secret-a") == session_id_for(token, "secret-a")
    assert session_id_for(token, "secret-a") != session_id_for(token, "secret-b")
    assert token not in session_id_for(token, "secret-a")


class TestRunToken:
    SECRET = "run-token-secret"
    RUN_ID = uuid.UUID(int=1)
    AGENT_ID = uuid.UUID(int=2)
    NOW = 1_000_000

    def _token(self, *, expires_at: int = NOW + 3600) -> str:
        return mint_run_token(
            run_id=self.RUN_ID, agent_id=self.AGENT_ID, expires_at=expires_at, secret=self.SECRET
        )

    def test_round_trips(self) -> None:
        claims = verify_run_token(self._token(), self.SECRET, now=self.NOW)
        assert claims is not None
        assert claims.run_id == self.RUN_ID
        assert claims.agent_id == self.AGENT_ID

    def test_wrong_secret_rejected(self) -> None:
        assert verify_run_token(self._token(), "wrong-secret", now=self.NOW) is None

    def test_expired_token_rejected(self) -> None:
        token = self._token(expires_at=self.NOW - 1)
        assert verify_run_token(token, self.SECRET, now=self.NOW) is None

    def test_expiry_boundary_is_inclusive(self) -> None:
        token = self._token(expires_at=self.NOW)
        assert verify_run_token(token, self.SECRET, now=self.NOW) is not None

    def test_tampered_payload_rejected(self) -> None:
        """A different run_id's payload paired with this token's original signature."""
        _, signature = self._token().split(".", 1)
        other_payload, _ = self._token(expires_at=self.NOW + 7200).split(".", 1)
        assert verify_run_token(f"{other_payload}.{signature}", self.SECRET, now=self.NOW) is None

    def test_tampered_signature_rejected(self) -> None:
        payload_b64, _signature = self._token().split(".", 1)
        assert verify_run_token(f"{payload_b64}.deadbeef", self.SECRET, now=self.NOW) is None

    def test_malformed_token_rejected(self) -> None:
        assert verify_run_token("not-a-token", self.SECRET, now=self.NOW) is None
        assert verify_run_token("", self.SECRET, now=self.NOW) is None
        assert verify_run_token("a.b.c", self.SECRET, now=self.NOW) is None

    def test_token_does_not_leak_secret(self) -> None:
        assert self.SECRET not in self._token()

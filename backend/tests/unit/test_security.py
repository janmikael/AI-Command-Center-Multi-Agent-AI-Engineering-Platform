from acc.security import (
    hash_password,
    needs_rehash,
    new_session_token,
    session_id_for,
    verify_password,
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

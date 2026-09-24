from datetime import UTC, datetime, timedelta

import pytest

from acc.domain.auth import MIN_PASSWORD_LENGTH, check_password_policy, session_expiry
from acc.domain.errors import ValidationFailed

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


class TestPasswordPolicy:
    def test_accepts_a_reasonable_passphrase(self) -> None:
        check_password_policy("correct horse battery staple")

    @pytest.mark.parametrize(
        ("password", "reason"),
        [
            ("x" * (MIN_PASSWORD_LENGTH - 1), "at least"),
            ("a" * 257, "at most"),
            ("abababababababab", "repetitive"),
        ],
    )
    def test_rejects(self, password: str, reason: str) -> None:
        with pytest.raises(ValidationFailed, match=reason):
            check_password_policy(password)


class TestSessionExpiry:
    def test_slides_by_idle_window(self) -> None:
        absolute = NOW + timedelta(days=7)
        assert session_expiry(now=NOW, absolute_expires_at=absolute, idle=timedelta(hours=12)) == (
            NOW + timedelta(hours=12)
        )

    def test_never_extends_past_absolute_limit(self) -> None:
        absolute = NOW + timedelta(hours=1)
        assert (
            session_expiry(now=NOW, absolute_expires_at=absolute, idle=timedelta(hours=12))
            == absolute
        )

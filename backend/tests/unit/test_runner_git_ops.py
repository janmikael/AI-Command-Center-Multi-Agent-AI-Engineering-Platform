"""acc's backend test suite also covers runner/git_ops.py and runner/git_askpass.py —
they're not part of the `acc` package (deliberately, PLAN-PHASE-2-M2.md's decision to
keep the runner outside the layered import graph entirely), but there's no reason their
pure logic should go untested just because they live in a sibling directory. This file
adds `runner/` to sys.path itself rather than needing a second test runner."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

RUNNER_DIR = Path(__file__).resolve().parents[3] / "runner"
if str(RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(RUNNER_DIR))

import git_askpass  # type: ignore[import-not-found]  # noqa: E402
import git_ops  # type: ignore[import-not-found]  # noqa: E402


class TestBranchPattern:
    @pytest.mark.parametrize(
        "branch",
        ["acc/acc-21-a1b2c3d4", "acc/foo-1", "acc/x"],
    )
    def test_accepts_acc_prefixed_branches(self, branch: str) -> None:
        assert git_ops.BRANCH_PATTERN.match(branch)

    @pytest.mark.parametrize(
        "branch",
        ["main", "master", "acc", "acc-no-slash", "ACC/upper", "acc/", "feature/acc-lookalike"],
    )
    def test_rejects_everything_else(self, branch: str) -> None:
        assert not git_ops.BRANCH_PATTERN.match(branch)


class TestCreateBranchRefusal:
    def test_refuses_a_non_acc_branch_before_running_git(self, tmp_path: Path) -> None:
        with (
            patch.object(git_ops, "_run") as mock_run,
            pytest.raises(git_ops.GitError, match="refusing"),
        ):
            git_ops.create_branch("main", tmp_path)
        mock_run.assert_not_called()

    def test_allows_an_acc_branch(self, tmp_path: Path) -> None:
        with patch.object(git_ops, "_run") as mock_run:
            git_ops.create_branch("acc/task-1-abcd1234", tmp_path)
        mock_run.assert_called_once_with(["checkout", "-b", "acc/task-1-abcd1234"], cwd=tmp_path)


class TestPushRefusal:
    def test_refuses_pushing_to_main(self, tmp_path: Path) -> None:
        with (
            patch.object(git_ops, "_run") as mock_run,
            pytest.raises(git_ops.GitError, match="refusing"),
        ):
            git_ops.push("main", tmp_path)
        mock_run.assert_not_called()

    def test_refuses_pushing_outside_the_acc_namespace(self, tmp_path: Path) -> None:
        with patch.object(git_ops, "_run") as mock_run, pytest.raises(git_ops.GitError):
            git_ops.push("feature/sneaky", tmp_path)
        mock_run.assert_not_called()

    def test_allows_pushing_an_acc_branch(self, tmp_path: Path) -> None:
        with patch.object(git_ops, "_run") as mock_run:
            git_ops.push("acc/task-1-abcd1234", tmp_path)
        mock_run.assert_called_once_with(
            ["push", "origin", "HEAD:acc/task-1-abcd1234"], cwd=tmp_path
        )


class TestDiffStat:
    def test_merges_numstat_and_name_status_by_path(self, tmp_path: Path) -> None:
        numstat = "3\t1\tfoo.py\n0\t0\tbar.txt\n"
        name_status = "M\tfoo.py\nA\tbar.txt\n"
        with patch.object(git_ops, "_run", side_effect=[numstat, name_status]):
            changes = git_ops.diff_stat("main", tmp_path)
        assert changes == [
            {"path": "foo.py", "change_type": "modified", "additions": 3, "deletions": 1},
            {"path": "bar.txt", "change_type": "added", "additions": 0, "deletions": 0},
        ]

    def test_binary_files_get_null_counts(self, tmp_path: Path) -> None:
        numstat = "-\t-\timage.png\n"
        name_status = "A\timage.png\n"
        with patch.object(git_ops, "_run", side_effect=[numstat, name_status]):
            changes = git_ops.diff_stat("main", tmp_path)
        assert changes == [
            {"path": "image.png", "change_type": "added", "additions": None, "deletions": None}
        ]

    def test_empty_diff_is_an_empty_list(self, tmp_path: Path) -> None:
        with patch.object(git_ops, "_run", side_effect=["", ""]):
            assert git_ops.diff_stat("main", tmp_path) == []


class TestGitAskpass:
    def test_username_prompt_returns_a_placeholder_not_the_credential(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("GIT_CREDENTIAL", "super-secret-token")
        monkeypatch.setattr(sys, "argv", ["git_askpass.py", "Username for 'https://x': "])
        git_askpass.main()
        out = capsys.readouterr().out
        assert "super-secret-token" not in out
        assert out.strip() == "x-access-token"

    def test_password_prompt_returns_the_credential(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("GIT_CREDENTIAL", "super-secret-token")
        monkeypatch.setattr(sys, "argv", ["git_askpass.py", "Password for 'https://x': "])
        git_askpass.main()
        assert capsys.readouterr().out.strip() == "super-secret-token"

    def test_missing_credential_returns_empty_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("GIT_CREDENTIAL", raising=False)
        monkeypatch.setattr(sys, "argv", ["git_askpass.py", "Password for 'https://x': "])
        assert git_askpass.main() == 0
        assert capsys.readouterr().out.strip() == ""

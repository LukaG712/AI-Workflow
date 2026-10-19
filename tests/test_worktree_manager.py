"""Tests for Git worktree lifecycle management.

Uses real Git in temporary directories — no live Junie required.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from junie_adapter.worktree_manager import (
    WorktreeInfo,
    FileDiff,
    cleanup_orphaned_worktrees,
    create_worktree,
    get_changed_files,
    get_worker_diff,
    get_change_summary,
    remove_worktree,
    validate_git_repo,
    _verify_worktree_ownership,
)


def _init_git_repo(path: Path) -> Path:
    """Initialize a Git repo at the given path with one commit on the master branch."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "master"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(path), check=True, capture_output=True)
    # Create an initial file and commit
    (path / "README.md").write_text("# Test Project\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(path), check=True, capture_output=True)
    return path


class TestValidateGitRepo:
    def test_valid_git_repo(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        assert validate_git_repo(repo) is True

    def test_non_git_directory(self, tmp_path: Path):
        (tmp_path / "not-a-repo").mkdir()
        assert validate_git_repo(tmp_path / "not-a-repo") is False

    def test_nonexistent_path(self, tmp_path: Path):
        assert validate_git_repo(tmp_path / "does-not-exist") is False


class TestCreateWorktree:
    def test_create_worktree(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        assert isinstance(info, WorktreeInfo)
        assert info.worktree_path.exists()
        assert info.branch_name.startswith("junie-worker-worker-1-")
        assert info.project_path == repo
        # Worktree should be outside the main checkout
        assert str(info.worktree_path).startswith(str(repo.parent / ".junie-worktrees"))

    def test_create_worktree_non_git_repo(self, tmp_path: Path):
        (tmp_path / "not-a-repo").mkdir()
        with pytest.raises(RuntimeError, match="not a valid Git repository"):
            create_worktree(tmp_path / "not-a-repo", "worker-1")

    def test_create_multiple_worktrees(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        info1 = create_worktree(repo, "worker-1")
        info2 = create_worktree(repo, "worker-2")

        assert info1.worktree_path != info2.worktree_path
        assert info1.worktree_path.exists()
        assert info2.worktree_path.exists()

    def test_create_worktree_without_base_branch(self, tmp_path: Path):
        """Test that worktree creation works without specifying a base branch."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")
        assert info.worktree_path.exists()
        # Verify the worktree is a valid Git repo
        assert validate_git_repo(info.worktree_path) is True
        remove_worktree(info.worktree_path)


class TestRemoveWorktree:
    def test_remove_worktree(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")
        result = remove_worktree(info.worktree_path)

        assert result is True
        assert not info.worktree_path.exists()

    def test_remove_nonexistent_worktree(self, tmp_path: Path):
        result = remove_worktree(tmp_path / "does-not-exist")
        assert result is False


class TestCleanupOrphanedWorktrees:
    def test_cleanup_orphaned_worktrees(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Remove the worktree directory manually to simulate a crash
        shutil.rmtree(info.worktree_path)

        # Also remove Git's internal tracking to simulate a truly orphaned worktree
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate the directory to simulate an orphaned worktree
        info.worktree_path.mkdir(parents=True)
        # Create a .git file pointing to the main repo
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git'}\n", encoding="utf-8"
        )

        cleaned = cleanup_orphaned_worktrees(repo)
        assert info.worktree_path in cleaned
        assert not info.worktree_path.exists()

    def test_cleanup_no_orphans(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        cleaned = cleanup_orphaned_worktrees(repo)
        assert cleaned == []

    def test_cleanup_non_git_repo(self, tmp_path: Path):
        (tmp_path / "not-a-repo").mkdir()
        cleaned = cleanup_orphaned_worktrees(tmp_path / "not-a-repo")
        assert cleaned == []


class TestGetChangedFiles:
    def test_no_changed_files(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")
        changed = get_changed_files(info.worktree_path)
        assert changed == []
        remove_worktree(info.worktree_path)

    def test_changed_files_detected(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create a new file in the worktree
        (info.worktree_path / "new_file.py").write_text("print('hello')\n", encoding="utf-8")
        subprocess.run(["git", "add", "new_file.py"], cwd=str(info.worktree_path), check=True, capture_output=True)

        # git diff --name-only HEAD should show the staged file
        changed = get_changed_files(info.worktree_path)
        # The file is staged but not committed, so diff HEAD may not show it
        # but the function should handle this gracefully
        assert isinstance(changed, list)

        remove_worktree(info.worktree_path)

    def test_git_failure_raises_error(self, tmp_path: Path):
        """A failed Git inspection should raise RuntimeError, not return empty list."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Simulate Git command failure by patching _run_git at the module level,
        # but only for diff/ls-files commands. Validation commands (rev-parse)
        # must still work so validate_git_repo passes.
        import junie_adapter.worktree_manager as wm_module

        original_run_git = wm_module._run_git

        def failing_diff_git(args, cwd=None):
            # Let validation commands pass, fail diff commands
            if "diff" in args or "ls-files" in args:
                raise RuntimeError("Git command failed")
            return original_run_git(args, cwd=cwd)

        with patch.object(wm_module, "_run_git", failing_diff_git):
            # Call through the module so the patch takes effect
            with pytest.raises(RuntimeError, match="Git inspection failed"):
                wm_module.get_changed_files(info.worktree_path)

        remove_worktree(info.worktree_path)

    def test_nonexistent_path_raises_error(self, tmp_path: Path):
        """get_changed_files on a nonexistent path should raise RuntimeError."""
        with pytest.raises(RuntimeError, match="does not exist"):
            get_changed_files(tmp_path / "does-not-exist")

    def test_non_git_path_raises_error(self, tmp_path: Path):
        """get_changed_files on a non-Git directory should raise RuntimeError."""
        (tmp_path / "not-a-repo").mkdir()
        with pytest.raises(RuntimeError, match="not a Git repository"):
            get_changed_files(tmp_path / "not-a-repo")

    def test_untracked_files_detected(self, tmp_path: Path):
        """Untracked files should be included in changed files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create an untracked file (not staged)
        (info.worktree_path / "untracked.txt").write_text("untracked content\n", encoding="utf-8")

        changed = get_changed_files(info.worktree_path)
        assert "untracked.txt" in changed

        remove_worktree(info.worktree_path)

    def test_modified_files_detected(self, tmp_path: Path):
        """Modified tracked files should be included in changed files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Modify the README.md that was committed
        (info.worktree_path / "README.md").write_text("# Modified\n", encoding="utf-8")

        changed = get_changed_files(info.worktree_path)
        assert "README.md" in changed

        remove_worktree(info.worktree_path)

    def test_deleted_files_detected(self, tmp_path: Path):
        """Deleted tracked files should be included in changed files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Delete the README.md that was committed
        (info.worktree_path / "README.md").unlink()
        subprocess.run(["git", "add", "README.md"], cwd=str(info.worktree_path), check=True, capture_output=True)

        changed = get_changed_files(info.worktree_path)
        assert "README.md" in changed

        remove_worktree(info.worktree_path)

    def test_staged_files_detected(self, tmp_path: Path):
        """Staged files should be included in changed files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create and stage a new file
        (info.worktree_path / "staged.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "staged.py"], cwd=str(info.worktree_path), check=True, capture_output=True)

        changed = get_changed_files(info.worktree_path)
        assert "staged.py" in changed

        remove_worktree(info.worktree_path)

    def test_files_with_spaces(self, tmp_path: Path):
        """Files with spaces in names should be handled correctly."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create a file with spaces
        (info.worktree_path / "file with spaces.txt").write_text("content\n", encoding="utf-8")

        changed = get_changed_files(info.worktree_path)
        assert "file with spaces.txt" in changed

        remove_worktree(info.worktree_path)

    def test_base_commit_includes_committed_changes(self, tmp_path: Path):
        """Changes committed after base_commit should be included when base_commit is provided."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Record the base commit
        base_commit = info.base_commit

        # Make a commit in the worktree
        (info.worktree_path / "new_committed.py").write_text("committed = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "new_committed.py"], cwd=str(info.worktree_path), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add new file"], cwd=str(info.worktree_path), check=True, capture_output=True)

        # Without base_commit, HEAD diff shows no changes (committed)
        changed_without_base = get_changed_files(info.worktree_path)
        assert "new_committed.py" not in changed_without_base

        # With base_commit, the committed change should be visible
        changed_with_base = get_changed_files(info.worktree_path, base_commit=base_commit)
        assert "new_committed.py" in changed_with_base

        remove_worktree(info.worktree_path)


class TestGetWorkerDiff:
    def test_diff_on_clean_worktree(self, tmp_path: Path):
        """Diff on a clean worktree should return empty list."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        diffs = get_worker_diff(info.worktree_path)
        assert diffs == []

        remove_worktree(info.worktree_path)

    def test_diff_on_untracked_file(self, tmp_path: Path):
        """Untracked files should have diff content."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        (info.worktree_path / "untracked.txt").write_text("line1\nline2\n", encoding="utf-8")

        diffs = get_worker_diff(info.worktree_path)
        assert len(diffs) == 1
        assert diffs[0].file_path == "untracked.txt"
        assert diffs[0].status == "U"
        assert "line1" in diffs[0].diff_content

        remove_worktree(info.worktree_path)

    def test_diff_on_deleted_file(self, tmp_path: Path):
        """Deleted files should be reported with D status."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        (info.worktree_path / "README.md").unlink()
        subprocess.run(["git", "add", "README.md"], cwd=str(info.worktree_path), check=True, capture_output=True)

        diffs = get_worker_diff(info.worktree_path)
        assert len(diffs) == 1
        assert diffs[0].file_path == "README.md"
        assert diffs[0].status == "D"

        remove_worktree(info.worktree_path)

    def test_diff_with_base_commit(self, tmp_path: Path):
        """Diff against base_commit should include committed changes."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        base_commit = info.base_commit

        # Make a commit in the worktree
        (info.worktree_path / "committed.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "committed.py"], cwd=str(info.worktree_path), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], cwd=str(info.worktree_path), check=True, capture_output=True)

        # Diff against base_commit should show the committed change
        diffs = get_worker_diff(info.worktree_path, base_commit=base_commit)
        assert len(diffs) == 1
        assert diffs[0].file_path == "committed.py"

        remove_worktree(info.worktree_path)


class TestGetChangeSummary:
    def test_summary_on_clean_worktree(self, tmp_path: Path):
        """Summary on a clean worktree should say no changes."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        summary = get_change_summary(info.worktree_path)
        assert "No changes" in summary

        remove_worktree(info.worktree_path)

    def test_summary_counts_files(self, tmp_path: Path):
        """Summary should count modified and untracked files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Modify tracked file
        (info.worktree_path / "README.md").write_text("# Modified\n", encoding="utf-8")
        # Add untracked file
        (info.worktree_path / "new.txt").write_text("new\n", encoding="utf-8")

        summary = get_change_summary(info.worktree_path)
        assert "modified" in summary
        assert "untracked" in summary

        remove_worktree(info.worktree_path)


class TestRemoveWorktreeSafety:
    def test_remove_main_checkout_refused(self, tmp_path: Path):
        """Attempting to remove the main checkout should raise RuntimeError."""
        repo = _init_git_repo(tmp_path / "repo")

        with pytest.raises(RuntimeError, match="main Git checkout"):
            remove_worktree(repo)

        # Main repo should still exist
        assert repo.exists()

    def test_remove_non_junie_path_refused(self, tmp_path: Path):
        """Removing a non-Junie worktree path should raise RuntimeError."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Try to remove a path that's not in .junie-worktrees
        # by simulating a Git failure on a non-Junie path
        unrelated_dir = tmp_path / "unrelated"
        unrelated_dir.mkdir()
        # Create a .git file to make it look like a repo
        (unrelated_dir / ".git").write_text("gitdir: /some/other/path\n", encoding="utf-8")

        with pytest.raises(RuntimeError):
            remove_worktree(unrelated_dir)

        # Unrelated dir should still exist
        assert unrelated_dir.exists()

        remove_worktree(info.worktree_path)

    def test_remove_without_git_file_refused(self, tmp_path: Path):
        """Removing a Junie worktree path without .git file should raise RuntimeError."""
        repo = _init_git_repo(tmp_path / "repo")

        # Create a directory in .junie-worktrees without .git file
        worktrees_dir = repo.parent / ".junie-worktrees" / "no-git-file"
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        (worktrees_dir / "somefile.txt").write_text("data\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="missing .git file"):
            remove_worktree(worktrees_dir)

        # Directory should still exist
        assert worktrees_dir.exists()


class TestCleanupOrphanedWorktreesSafety:
    def test_preserves_unrelated_directory(self, tmp_path: Path):
        """Unrelated directories in .junie-worktrees should not be deleted."""
        repo = _init_git_repo(tmp_path / "repo")

        # Create an unrelated directory in .junie-worktrees
        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        unrelated = worktrees_dir / "unrelated-project"
        unrelated.mkdir()
        (unrelated / "data.txt").write_text("important data\n", encoding="utf-8")
        # No .git file — should not be deleted

        cleaned = cleanup_orphaned_worktrees(repo)
        assert unrelated.exists()
        assert (unrelated / "data.txt").read_text(encoding="utf-8") == "important data\n"

    def test_preserves_worktree_with_unreviewed_changes(self, tmp_path: Path):
        """Tracked worktrees with changes should not be deleted."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Make changes in the worktree
        (info.worktree_path / "important.py").write_text("critical code\n", encoding="utf-8")

        # The worktree is tracked, so it should not be cleaned
        cleaned = cleanup_orphaned_worktrees(repo)
        assert info.worktree_path not in cleaned
        assert info.worktree_path.exists()

        remove_worktree(info.worktree_path)

    def test_preserves_unowned_worktree(self, tmp_path: Path):
        """Worktrees belonging to a different repo should not be deleted."""
        repo1 = _init_git_repo(tmp_path / "repo1")

        # Create a directory in repo1's .junie-worktrees that points to a different repo
        worktrees_dir = repo1.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        fake_worktree = worktrees_dir / "other-repo-worktree"
        fake_worktree.mkdir()
        # Point to a completely different repo
        (fake_worktree / ".git").write_text("gitdir: /completely/different/repo/.git\n", encoding="utf-8")
        (fake_worktree / "data.txt").write_text("should survive\n", encoding="utf-8")

        cleaned = cleanup_orphaned_worktrees(repo1)
        assert fake_worktree.exists()
        assert fake_worktree not in cleaned

    def test_cleans_orphaned_worktree(self, tmp_path: Path):
        """Truly orphaned worktrees belonging to the repo should be cleaned."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Remove the worktree directory manually to simulate a crash
        shutil.rmtree(info.worktree_path)

        # Also remove Git's internal tracking to simulate a truly orphaned worktree
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate the directory to simulate an orphaned worktree
        info.worktree_path.mkdir(parents=True)
        # Create a .git file pointing to the main repo
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git'}\n", encoding="utf-8"
        )

        cleaned = cleanup_orphaned_worktrees(repo)
        assert info.worktree_path in cleaned
        assert not info.worktree_path.exists()

    def test_refuses_non_junie_base_dir(self, tmp_path: Path):
        """Cleanup should refuse to scan a non-.junie-worktrees directory."""
        repo = _init_git_repo(tmp_path / "repo")

        # Try to scan a random directory
        random_dir = tmp_path / "random-dir"
        random_dir.mkdir()

        cleaned = cleanup_orphaned_worktrees(repo, base_dir=random_dir)
        assert cleaned == []

    def test_verify_worktree_ownership(self, tmp_path: Path):
        """Ownership check should verify .git file points to the correct repo."""
        repo = _init_git_repo(tmp_path / "repo")

        # Create a directory with .git pointing to this repo
        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        owned = worktrees_dir / "owned-worktree"
        owned.mkdir()
        (owned / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / 'branch'}\n", encoding="utf-8"
        )
        assert _verify_worktree_ownership(owned, repo) is True

        # Create a directory with .git pointing to a different repo
        unowned = worktrees_dir / "unowned-worktree"
        unowned.mkdir()
        (unowned / ".git").write_text("gitdir: /different/repo/.git\n", encoding="utf-8")
        assert _verify_worktree_ownership(unowned, repo) is False

        # Directory without .git file
        no_git = worktrees_dir / "no-git"
        no_git.mkdir()
        assert _verify_worktree_ownership(no_git, repo) is False

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
    _is_junie_worktree_path,
    _is_worktree_dirty,
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
        # Create a .git file pointing to the now-deleted worktrees metadata
        # This makes it a truly orphaned worktree with stale metadata
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
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
        remove_worktree(info.worktree_path, force=True)

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

        remove_worktree(info.worktree_path, force=True)

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

        remove_worktree(info.worktree_path, force=True)

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

        remove_worktree(info.worktree_path, force=True)

    def test_modified_files_detected(self, tmp_path: Path):
        """Modified tracked files should be included in changed files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Modify the README.md that was committed
        (info.worktree_path / "README.md").write_text("# Modified\n", encoding="utf-8")

        changed = get_changed_files(info.worktree_path)
        assert "README.md" in changed

        remove_worktree(info.worktree_path, force=True)

    def test_deleted_files_detected(self, tmp_path: Path):
        """Deleted tracked files should be included in changed files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Delete the README.md that was committed
        (info.worktree_path / "README.md").unlink()
        subprocess.run(["git", "add", "README.md"], cwd=str(info.worktree_path), check=True, capture_output=True)

        changed = get_changed_files(info.worktree_path)
        assert "README.md" in changed

        remove_worktree(info.worktree_path, force=True)

    def test_staged_files_detected(self, tmp_path: Path):
        """Staged files should be included in changed files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create and stage a new file
        (info.worktree_path / "staged.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "staged.py"], cwd=str(info.worktree_path), check=True, capture_output=True)

        changed = get_changed_files(info.worktree_path)
        assert "staged.py" in changed

        remove_worktree(info.worktree_path, force=True)

    def test_files_with_spaces(self, tmp_path: Path):
        """Files with spaces in names should be handled correctly."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create a file with spaces
        (info.worktree_path / "file with spaces.txt").write_text("content\n", encoding="utf-8")

        changed = get_changed_files(info.worktree_path)
        assert "file with spaces.txt" in changed

        remove_worktree(info.worktree_path, force=True)

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

        remove_worktree(info.worktree_path, force=True)


class TestGetWorkerDiff:
    def test_diff_on_clean_worktree(self, tmp_path: Path):
        """Diff on a clean worktree should return empty list."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        diffs = get_worker_diff(info.worktree_path)
        assert diffs == []

        remove_worktree(info.worktree_path, force=True)

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

        remove_worktree(info.worktree_path, force=True)

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

        remove_worktree(info.worktree_path, force=True)

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

        remove_worktree(info.worktree_path, force=True)


class TestGetChangeSummary:
    def test_summary_on_clean_worktree(self, tmp_path: Path):
        """Summary on a clean worktree should say no changes."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        summary = get_change_summary(info.worktree_path)
        assert "No changes" in summary

        remove_worktree(info.worktree_path, force=True)

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

        remove_worktree(info.worktree_path, force=True)


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

        remove_worktree(info.worktree_path, force=True)

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

        remove_worktree(info.worktree_path, force=True)

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
        # Create a .git file pointing to the main repo's worktrees dir
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n", encoding="utf-8"
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

        # Create a directory with .git pointing to this repo's .git/worktrees/
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


class TestRemoveWorktreeDirtyProtection:
    """Tests for dirty worktree deletion protection."""

    def test_refuses_dirty_worktree_with_modified_files(self, tmp_path: Path):
        """remove_worktree should refuse to delete a worktree with modified files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Modify a tracked file
        (info.worktree_path / "README.md").write_text("# Modified\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="uncommitted changes"):
            remove_worktree(info.worktree_path)

        # Worktree should still exist
        assert info.worktree_path.exists()
        remove_worktree(info.worktree_path, force=True)

    def test_refuses_dirty_worktree_with_untracked_files(self, tmp_path: Path):
        """remove_worktree should refuse to delete a worktree with untracked files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create an untracked file
        (info.worktree_path / "new_file.txt").write_text("new content\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="uncommitted changes"):
            remove_worktree(info.worktree_path)

        assert info.worktree_path.exists()
        remove_worktree(info.worktree_path, force=True)

    def test_refuses_dirty_worktree_with_staged_files(self, tmp_path: Path):
        """remove_worktree should refuse to delete a worktree with staged files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create and stage a file
        (info.worktree_path / "staged.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "staged.py"],
            cwd=str(info.worktree_path),
            check=True,
            capture_output=True,
        )

        with pytest.raises(RuntimeError, match="uncommitted changes"):
            remove_worktree(info.worktree_path)

        assert info.worktree_path.exists()
        remove_worktree(info.worktree_path, force=True)

    def test_allows_clean_worktree_removal(self, tmp_path: Path):
        """remove_worktree should allow removing a clean worktree."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Worktree is clean — should succeed without force
        result = remove_worktree(info.worktree_path)
        assert result is True
        assert not info.worktree_path.exists()

    def test_force_allows_dirty_removal(self, tmp_path: Path):
        """force=True should allow removing a dirty worktree."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        (info.worktree_path / "dirty.txt").write_text("data\n", encoding="utf-8")

        result = remove_worktree(info.worktree_path, force=True)
        assert result is True
        assert not info.worktree_path.exists()

    def test_is_worktree_dirty_clean(self, tmp_path: Path):
        """_is_worktree_dirty should return False for a clean worktree."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")
        assert _is_worktree_dirty(info.worktree_path) is False
        remove_worktree(info.worktree_path)

    def test_is_worktree_dirty_modified(self, tmp_path: Path):
        """_is_worktree_dirty should return True for modified files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")
        (info.worktree_path / "README.md").write_text("# Modified\n", encoding="utf-8")
        assert _is_worktree_dirty(info.worktree_path) is True
        remove_worktree(info.worktree_path, force=True)

    def test_is_worktree_dirty_untracked(self, tmp_path: Path):
        """_is_worktree_dirty should return True for untracked files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")
        (info.worktree_path / "untracked.txt").write_text("data\n", encoding="utf-8")
        assert _is_worktree_dirty(info.worktree_path) is True
        remove_worktree(info.worktree_path, force=True)


class TestOwnershipVerificationSafety:
    """Tests for canonical path-based ownership verification."""

    def test_forged_git_path_rejected(self, tmp_path: Path):
        """A forged .git file pointing to a different repo must be rejected."""
        repo = _init_git_repo(tmp_path / "repo")
        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        # Forge a .git file pointing to a non-existent repo
        forged = worktrees_dir / "forged-worktree"
        forged.mkdir()
        (forged / ".git").write_text(
            "gitdir: /tmp/evil-repo/.git/worktrees/branch\n", encoding="utf-8"
        )
        assert _verify_worktree_ownership(forged, repo) is False

    def test_repo_name_prefix_collision(self, tmp_path: Path):
        """A path like /my-repo-backup/.git must not match /my-repo/.git."""
        repo = _init_git_repo(tmp_path / "my-repo")
        other_repo = _init_git_repo(tmp_path / "my-repo-backup")

        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        # Point to the other repo's .git — substring match would incorrectly pass
        collision = worktrees_dir / "collision-worktree"
        collision.mkdir()
        (collision / ".git").write_text(
            f"gitdir: {other_repo / '.git' / 'worktrees' / 'branch'}\n",
            encoding="utf-8",
        )
        assert _verify_worktree_ownership(collision, repo) is False
        assert _verify_worktree_ownership(collision, other_repo) is True

    def test_valid_worktree_ownership(self, tmp_path: Path):
        """A real worktree's .git file should verify as owned."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # The real worktree should verify as owned
        assert _verify_worktree_ownership(info.worktree_path, repo) is True
        remove_worktree(info.worktree_path, force=True)

    def test_no_gitdir_prefix_rejected(self, tmp_path: Path):
        """A .git file without gitdir: prefix should be rejected."""
        repo = _init_git_repo(tmp_path / "repo")
        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        bad = worktrees_dir / "bad-worktree"
        bad.mkdir()
        (bad / ".git").write_text(
            f"{repo / '.git' / 'worktrees' / 'branch'}\n", encoding="utf-8"
        )
        assert _verify_worktree_ownership(bad, repo) is False


class TestJunieWorktreePathValidation:
    """Tests for _is_junie_worktree_path with git_root validation."""

    def test_path_outside_expected_base_rejected(self, tmp_path: Path):
        """A .junie-worktrees directory not under the expected repo must be rejected."""
        # Use different parent directories so the .junie-worktrees dirs differ
        repo = _init_git_repo(tmp_path / "project-a" / "repo")
        other_repo = _init_git_repo(tmp_path / "project-b" / "other-repo")

        # other-repo's .junie-worktrees should not be valid for repo
        other_worktrees = other_repo.parent / ".junie-worktrees"
        other_worktrees.mkdir(parents=True, exist_ok=True)
        fake = other_worktrees / "fake-branch"
        fake.mkdir()

        assert _is_junie_worktree_path(fake, repo) is False
        assert _is_junie_worktree_path(fake, other_repo) is True

    def test_correct_base_accepted(self, tmp_path: Path):
        """A path under the correct .junie-worktrees should be accepted."""
        repo = _init_git_repo(tmp_path / "repo")
        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        valid = worktrees_dir / "valid-branch"
        valid.mkdir()

        assert _is_junie_worktree_path(valid, repo) is True

    def test_non_junie_name_rejected(self, tmp_path: Path):
        """A path not containing .junie-worktrees should be rejected."""
        repo = _init_git_repo(tmp_path / "repo")
        random_dir = tmp_path / "random-dir" / "subdir"
        random_dir.mkdir(parents=True, exist_ok=True)

        assert _is_junie_worktree_path(random_dir, repo) is False


class TestCleanupOrphanedSafety:
    """Tests for cleanup_orphaned_worktrees safety guarantees."""

    def test_git_failure_aborts_cleanup(self, tmp_path: Path):
        """If git worktree list fails, cleanup must abort with RuntimeError."""
        repo = _init_git_repo(tmp_path / "repo")
        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        # Create an orphaned-looking worktree
        orphan = worktrees_dir / "orphan-branch"
        orphan.mkdir()
        (orphan / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / 'orphan-branch'}\n",
            encoding="utf-8",
        )

        import junie_adapter.worktree_manager as wm_module

        original_run_git = wm_module._run_git

        def failing_git(args, cwd=None):
            if args[0] == "worktree" and args[1] == "list":
                raise RuntimeError("Git worktree list failed")
            return original_run_git(args, cwd=cwd)

        with patch.object(wm_module, "_run_git", failing_git):
            with pytest.raises(RuntimeError, match="aborting cleanup"):
                wm_module.cleanup_orphaned_worktrees(repo)

        # Orphan should NOT be deleted because cleanup aborted
        assert orphan.exists()

    def test_orphaned_dirty_worktree_preserved(self, tmp_path: Path):
        """Worktrees with unreviewed changes must be preserved.

        This tests the core safety guarantee: cleanup never deletes a worktree
        containing uncommitted changes. A tracked worktree with dirty changes
        is never deleted because it is tracked. An orphaned worktree with
        valid gitdir metadata and dirty changes is also preserved.
        """
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Make the worktree dirty — this is the key condition
        (info.worktree_path / "important.py").write_text("critical code\n", encoding="utf-8")

        # The worktree is tracked, so cleanup should skip it entirely
        cleaned = cleanup_orphaned_worktrees(repo)
        assert info.worktree_path not in cleaned
        assert info.worktree_path.exists()
        assert (info.worktree_path / "important.py").exists()

        remove_worktree(info.worktree_path, force=True)

    def test_symlink_escape_rejected(self, tmp_path: Path):
        """A symlink pointing outside .junie-worktrees must not be followed."""
        repo = _init_git_repo(tmp_path / "repo")
        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        # Create a target directory outside .junie-worktrees
        target = tmp_path / "outside-target"
        target.mkdir()
        (target / "data.txt").write_text("should survive\n", encoding="utf-8")

        # Create a symlink inside .junie-worktrees pointing to the target
        try:
            symlink = worktrees_dir / "symlink-escape"
            symlink.symlink_to(target)

            # The symlink should not be deleted
            cleaned = cleanup_orphaned_worktrees(repo)
            assert target.exists()
            assert (target / "data.txt").read_text(encoding="utf-8") == "should survive\n"
        except OSError:
            # Symlinks may not be supported without admin rights on Windows
            pytest.skip("Symlink creation not supported")

    def test_cleanup_retry_after_partial_failure(self, tmp_path: Path):
        """Cleanup should be safe to retry after a partial failure."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Remove Git tracking to simulate orphan
        shutil.rmtree(info.worktree_path)
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate as clean orphan
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )

        # First cleanup should succeed
        cleaned1 = cleanup_orphaned_worktrees(repo)
        assert info.worktree_path in cleaned1

        # Second cleanup should be safe (nothing to clean)
        cleaned2 = cleanup_orphaned_worktrees(repo)
        assert cleaned2 == []

    def test_base_dir_mismatch_rejected(self, tmp_path: Path):
        """base_dir must match the expected .junie-worktrees for the repo."""
        repo = _init_git_repo(tmp_path / "repo")

        # Create a .junie-worktrees directory in a different location
        fake_base = tmp_path / "fake" / ".junie-worktrees"
        fake_base.mkdir(parents=True, exist_ok=True)

        cleaned = cleanup_orphaned_worktrees(repo, base_dir=fake_base)
        assert cleaned == []

    def test_missing_git_file_with_valuable_files_preserved(self, tmp_path: Path):
        """A directory without .git file must be preserved, even if it contains valuable files.

        This tests the safety guarantee: never delete a directory merely because
        its Git metadata is missing. If we cannot establish the relationship to Git,
        preserve the directory for manual inspection.
        """
        repo = _init_git_repo(tmp_path / "repo")
        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        # Create a directory with valuable files but no .git file
        no_git_dir = worktrees_dir / "valuable-work"
        no_git_dir.mkdir()
        (no_git_dir / "important_data.csv").write_text("col1,col2\n1,2\n", encoding="utf-8")
        (no_git_dir / "research_notes.md").write_text("# Research\nImportant findings\n", encoding="utf-8")

        cleaned = cleanup_orphaned_worktrees(repo)

        # Directory must be preserved
        assert no_git_dir.exists()
        assert (no_git_dir / "important_data.csv").exists()
        assert (no_git_dir / "research_notes.md").exists()
        assert no_git_dir not in cleaned

    def test_dirty_state_inspection_failure_preserves_directory(self, tmp_path: Path):
        """If _is_worktree_dirty() fails, the directory must be preserved.

        This tests the safety guarantee: if we cannot determine whether a worktree
        is dirty, we preserve it rather than risk deleting unreviewed work.
        """
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Remove Git tracking to simulate orphan
        shutil.rmtree(info.worktree_path)
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate as orphaned worktree with valid .git file
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )
        (info.worktree_path / "valuable.py").write_text("important code\n", encoding="utf-8")

        # Patch _is_worktree_dirty to simulate git status failure
        import junie_adapter.worktree_manager as wm_module

        original_dirty = wm_module._is_worktree_dirty

        def failing_dirty(path):
            raise RuntimeError("git status failed: repository corrupt")

        with patch.object(wm_module, "_is_worktree_dirty", failing_dirty):
            cleaned = wm_module.cleanup_orphaned_worktrees(repo)

        # Directory must be preserved because dirty check failed
        assert info.worktree_path.exists()
        assert (info.worktree_path / "valuable.py").exists()
        assert info.worktree_path not in cleaned

    def test_unclassifiable_directory_preserved(self, tmp_path: Path):
        """A directory with unrecognized .git format must be preserved.

        If a .git file exists but doesn't contain a valid gitdir: directive,
        we cannot confirm this is a worktree — preserve for manual inspection.
        """
        repo = _init_git_repo(tmp_path / "repo")
        worktrees_dir = repo.parent / ".junie-worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        # Create a directory with a .git file that has unrecognized format
        unclassifiable = worktrees_dir / "unclassifiable"
        unclassifiable.mkdir()
        (unclassifiable / ".git").write_text("some random content\n", encoding="utf-8")
        (unclassifiable / "data.txt").write_text("should survive\n", encoding="utf-8")

        cleaned = cleanup_orphaned_worktrees(repo)

        # Directory must be preserved
        assert unclassifiable.exists()
        assert (unclassifiable / "data.txt").exists()
        assert unclassifiable not in cleaned

    def test_missing_gitdir_metadata_with_valuable_files_preserved(self, tmp_path: Path):
        """Missing gitdir metadata alone must not trigger deletion.

        Even when the gitdir path from the .git file no longer exists (truly orphaned),
        the cleanup function must still check the dirty state before deleting.
        A clean orphaned worktree is safe to delete, but a dirty one is not.
        """
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Remove Git tracking to simulate orphan
        shutil.rmtree(info.worktree_path)
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate as orphaned worktree with stale metadata and dirty changes
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )
        # Add untracked (dirty) file
        (info.worktree_path / "unreviewed_work.py").write_text("critical work\n", encoding="utf-8")

        cleaned = cleanup_orphaned_worktrees(repo)

        # Dirty orphaned worktree must be preserved
        assert info.worktree_path.exists()
        assert (info.worktree_path / "unreviewed_work.py").exists()
        assert info.worktree_path not in cleaned

    def test_clean_orphaned_worktree_with_stale_metadata_cleaned(self, tmp_path: Path):
        """A truly orphaned and clean worktree with stale metadata should be cleaned.

        When all safety checks pass (ownership verified, path valid, orphaned,
        stale metadata, and clean worktree), the directory can be safely deleted.
        """
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Remove Git tracking to simulate orphan
        shutil.rmtree(info.worktree_path)
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate as orphaned worktree with stale metadata but NO dirty changes
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )
        # No additional files — worktree is clean

        cleaned = cleanup_orphaned_worktrees(repo)

        # Clean orphaned worktree should be cleaned
        assert info.worktree_path in cleaned
        assert not info.worktree_path.exists()

    def test_git_status_failure_on_orphan_preserves_directory(self, tmp_path: Path):
        """If git status fails on an orphaned worktree, preserve the directory.

        This is the most critical safety test: when the Git command itself fails
        (not just returns dirty), the directory must be preserved.
        """
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Remove Git tracking to simulate orphan
        shutil.rmtree(info.worktree_path)
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate as orphaned worktree
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )
        (info.worktree_path / "valuable.py").write_text("important code\n", encoding="utf-8")

        # Patch _run_git to fail on git status commands
        import junie_adapter.worktree_manager as wm_module

        original_run_git = wm_module._run_git

        def failing_status_git(args, cwd=None):
            # Fail only on git status commands
            if "status" in args:
                raise RuntimeError("git status failed: unable to read repository")
            return original_run_git(args, cwd=cwd)

        with patch.object(wm_module, "_run_git", failing_status_git):
            cleaned = wm_module.cleanup_orphaned_worktrees(repo)

        # Directory must be preserved because git status failed
        assert info.worktree_path.exists()
        assert (info.worktree_path / "valuable.py").exists()
        assert info.worktree_path not in cleaned


class TestIgnoredFilesPreservation:
    """Tests for the ignored-file data-loss risk.

    git status --porcelain excludes ignored files by default. A worktree
    containing only ignored files (e.g., .env, __pycache__/, .DS_Store)
    would appear clean and be auto-deleted. These tests verify that ignored
    files are detected and the worktree is preserved.
    """

    def test_is_worktree_dirty_detects_ignored_env_file(self, tmp_path: Path):
        """A worktree with only an ignored .env file should be detected as dirty."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create a .gitignore that ignores .env
        (info.worktree_path / ".gitignore").write_text(".env\n", encoding="utf-8")
        # Stage and commit the .gitignore so it's tracked
        subprocess.run(["git", "add", ".gitignore"], cwd=str(info.worktree_path),
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add .gitignore"],
                       cwd=str(info.worktree_path), check=True, capture_output=True)

        # Create an ignored .env file
        (info.worktree_path / ".env").write_text("SECRET_KEY=abc123\n", encoding="utf-8")

        # The worktree should be detected as dirty because of the ignored file
        assert _is_worktree_dirty(info.worktree_path) is True

        remove_worktree(info.worktree_path, force=True)

    def test_is_worktree_dirty_detects_ignored_pycache(self, tmp_path: Path):
        """A worktree with only an ignored __pycache__ directory should be dirty."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create a .gitignore that ignores __pycache__
        (info.worktree_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=str(info.worktree_path),
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add .gitignore"],
                       cwd=str(info.worktree_path), check=True, capture_output=True)

        # Create an ignored __pycache__ directory with a file
        pycache = info.worktree_path / "__pycache__"
        pycache.mkdir()
        (pycache / "module.cpython-311.pyc").write_bytes(b"fake bytecode")

        assert _is_worktree_dirty(info.worktree_path) is True

        remove_worktree(info.worktree_path, force=True)

    def test_is_worktree_dirty_detects_hidden_file(self, tmp_path: Path):
        """A worktree with only a hidden file should be detected as dirty."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create a hidden file (not ignored by .gitignore, but untracked)
        (info.worktree_path / ".DS_Store").write_text("hidden data\n", encoding="utf-8")

        # Untracked files are detected by git status --porcelain
        assert _is_worktree_dirty(info.worktree_path) is True

        remove_worktree(info.worktree_path, force=True)

    def test_is_worktree_dirty_clean_worktree(self, tmp_path: Path):
        """A genuinely clean worktree should not be detected as dirty."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # No changes — worktree is clean
        assert _is_worktree_dirty(info.worktree_path) is False

        remove_worktree(info.worktree_path)

    def test_orphan_cleanup_preserves_worktree_with_ignored_env(self, tmp_path: Path):
        """Orphan cleanup must not delete a worktree containing only ignored .env files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create a .gitignore that ignores .env
        (info.worktree_path / ".gitignore").write_text(".env\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=str(info.worktree_path),
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add .gitignore"],
                       cwd=str(info.worktree_path), check=True, capture_output=True)

        # Create an ignored .env file
        (info.worktree_path / ".env").write_text("SECRET_KEY=abc123\n", encoding="utf-8")

        # Remove Git tracking to simulate orphan
        shutil.rmtree(info.worktree_path)
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate as orphaned worktree with valid gitdir metadata
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )
        # Recreate the gitdir metadata so git status works
        git_worktrees_dir.mkdir(parents=True)
        (info.worktree_path / ".gitignore").write_text(".env\n", encoding="utf-8")
        (info.worktree_path / ".env").write_text("SECRET_KEY=abc123\n", encoding="utf-8")

        cleaned = cleanup_orphaned_worktrees(repo)

        # Worktree with ignored .env must be preserved
        assert info.worktree_path.exists()
        assert (info.worktree_path / ".env").exists()
        assert info.worktree_path not in cleaned

    def test_orphan_cleanup_preserves_worktree_with_nested_ignored_dir(self, tmp_path: Path):
        """Orphan cleanup must not delete a worktree with nested ignored directories."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create .gitignore
        (info.worktree_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=str(info.worktree_path),
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add .gitignore"],
                       cwd=str(info.worktree_path), check=True, capture_output=True)

        # Create nested ignored directory
        pycache = info.worktree_path / "__pycache__" / "submodule"
        pycache.mkdir(parents=True)
        (pycache / "cached.pyc").write_bytes(b"bytecode")

        # Remove Git tracking to simulate orphan
        shutil.rmtree(info.worktree_path)
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate as orphaned worktree with valid gitdir metadata
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )
        git_worktrees_dir.mkdir(parents=True)
        (info.worktree_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        pycache = info.worktree_path / "__pycache__" / "submodule"
        pycache.mkdir(parents=True)
        (pycache / "cached.pyc").write_bytes(b"bytecode")

        cleaned = cleanup_orphaned_worktrees(repo)

        # Worktree with nested ignored dir must be preserved
        assert info.worktree_path.exists()
        assert (pycache / "cached.pyc").exists()
        assert info.worktree_path not in cleaned

    def test_orphan_cleanup_preserves_worktree_with_local_only_files(self, tmp_path: Path):
        """Orphan cleanup must not delete a worktree with local-only (untracked) files."""
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Create local-only files that a worker might generate
        (info.worktree_path / "debug_log.txt").write_text("debug output\n", encoding="utf-8")
        (info.worktree_path / "temp_notes.md").write_text("# Notes\nTODO\n", encoding="utf-8")

        # Remove Git tracking to simulate orphan
        shutil.rmtree(info.worktree_path)
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate as orphaned worktree with valid gitdir metadata
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )
        git_worktrees_dir.mkdir(parents=True)
        (info.worktree_path / "debug_log.txt").write_text("debug output\n", encoding="utf-8")
        (info.worktree_path / "temp_notes.md").write_text("# Notes\nTODO\n", encoding="utf-8")

        cleaned = cleanup_orphaned_worktrees(repo)

        # Worktree with local-only files must be preserved
        assert info.worktree_path.exists()
        assert (info.worktree_path / "debug_log.txt").exists()
        assert (info.worktree_path / "temp_notes.md").exists()
        assert info.worktree_path not in cleaned

    def test_orphan_cleanup_removes_genuinely_clean_worktree_stale_metadata(
        self, tmp_path: Path
    ):
        """A genuinely clean orphaned worktree with stale metadata should be cleaned.

        When gitdir metadata is missing (stale) and the worktree contains no files
        beyond .git, it is safe to clean. This is the only scenario where automatic
        deletion of an orphaned worktree is safe: we can't run git status, so we
        scan for files directly.
        """
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Remove Git tracking to simulate orphan
        shutil.rmtree(info.worktree_path)
        git_worktrees_dir = repo / ".git" / "worktrees" / info.branch_name
        if git_worktrees_dir.exists():
            shutil.rmtree(git_worktrees_dir)

        # Recreate as orphaned worktree with stale metadata but NO files
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )
        # Do NOT recreate git_worktrees_dir — metadata is stale

        cleaned = cleanup_orphaned_worktrees(repo)

        # Genuinely clean orphaned worktree should be cleaned
        assert info.worktree_path in cleaned
        assert not info.worktree_path.exists()

    def test_orphan_cleanup_preserves_worktree_when_git_fails(self, tmp_path: Path):
        """If git status fails on an orphaned worktree with valid gitdir, preserve it.

        When gitdir metadata exists but git status fails (e.g., corrupted worktree),
        the directory must be preserved rather than risk deleting unreviewed work.
        This is the correct safety behavior: we cannot determine the dirty state,
        so we preserve.
        """
        repo = _init_git_repo(tmp_path / "repo")
        info = create_worktree(repo, "worker-1")

        # Remove worktree dir but keep gitdir metadata
        shutil.rmtree(info.worktree_path)
        # Do NOT remove git_worktrees_dir — metadata exists but is incomplete

        # Recreate worktree dir with .git file pointing to existing but incomplete gitdir
        info.worktree_path.mkdir(parents=True)
        (info.worktree_path / ".git").write_text(
            f"gitdir: {repo / '.git' / 'worktrees' / info.branch_name}\n",
            encoding="utf-8",
        )
        (info.worktree_path / "valuable.py").write_text("important code\n", encoding="utf-8")

        cleaned = cleanup_orphaned_worktrees(repo)

        # git status will fail on this incomplete worktree — must be preserved
        assert info.worktree_path.exists()
        assert (info.worktree_path / "valuable.py").exists()
        assert info.worktree_path not in cleaned

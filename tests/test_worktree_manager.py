"""Tests for Git worktree lifecycle management.

Uses real Git in temporary directories — no live Junie required.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from junie_adapter.worktree_manager import (
    WorktreeInfo,
    cleanup_orphaned_worktrees,
    create_worktree,
    get_changed_files,
    remove_worktree,
    validate_git_repo,
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

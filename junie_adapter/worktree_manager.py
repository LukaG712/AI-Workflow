"""Git worktree lifecycle management for isolated worker execution.

Provides functions to create, remove, and clean up Git worktrees used by
individual Junie workers. Worktrees are created outside the main checkout
to avoid conflicts with the primary working directory.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from subprocess import run, PIPE, CalledProcessError
from typing import List, Optional

runs_logger = logging.getLogger("runs")


@dataclass
class WorktreeInfo:
    """Metadata about a created Git worktree."""
    worktree_path: Path
    branch_name: str
    project_path: Path


def _run_git(args: List[str], cwd: Optional[Path] = None) -> str:
    """Run a Git command and return stdout, raising on failure."""
    try:
        result = run(
            ["git"] + args,
            cwd=str(cwd) if cwd else None,
            stdout=PIPE,
            stderr=PIPE,
            check=True,
        )
        return result.stdout.decode("utf-8", errors="replace").strip()
    except CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else ""
        raise RuntimeError(f"Git command failed: {' '.join(args)}\n{stderr}") from e
    except FileNotFoundError:
        raise RuntimeError("Git binary not found. Ensure Git is installed and in PATH.")


def validate_git_repo(project_path: Path) -> bool:
    """Check if the given path is a valid Git repository.

    Args:
        project_path: Path to check.

    Returns:
        True if the path is a valid Git repo, False otherwise.
    """
    if not project_path.exists() or not project_path.is_dir():
        return False
    try:
        _run_git(["rev-parse", "--git-dir"], cwd=project_path)
        return True
    except RuntimeError:
        return False


def _get_git_root(project_path: Path) -> Path:
    """Return the Git repository root for the given path."""
    output = _run_git(["rev-parse", "--show-toplevel"], cwd=project_path)
    return Path(output)


def _make_branch_name(worker_id: str) -> str:
    """Generate a unique branch name for a worker."""
    timestamp = int(time.time())
    return f"junie-worker-{worker_id}-{timestamp}"


def _make_worktree_dir(project_path: Path, branch_name: str) -> Path:
    """Generate a worktree directory path outside the main checkout.

    Worktrees are placed in a `.junie-worktrees` directory alongside the
    project root to keep them organized and separate from the main checkout.
    """
    git_root = _get_git_root(project_path)
    worktrees_base = git_root.parent / ".junie-worktrees"
    return worktrees_base / branch_name


def create_worktree(
    project_path: Path,
    worker_id: str,
    base_branch: Optional[str] = None,
) -> WorktreeInfo:
    """Create a Git worktree for an isolated worker execution.

    The worktree is created outside the main checkout with a unique branch
    name derived from the worker ID and timestamp.

    Args:
        project_path: Path to the Git repository.
        worker_id: Unique identifier for this worker.
        base_branch: Branch to base the worktree on. Defaults to the current HEAD.

    Returns:
        WorktreeInfo with the worktree path, branch name, and project path.

    Raises:
        RuntimeError: If the path is not a Git repo or Git commands fail.
    """
    if not validate_git_repo(project_path):
        raise RuntimeError(
            f"{project_path} is not a valid Git repository. "
            "Worktree isolation requires a Git repo."
        )

    branch_name = _make_branch_name(worker_id)
    worktree_dir = _make_worktree_dir(project_path, branch_name)

    # Ensure the base worktrees directory exists
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    # Build the git worktree add command
    cmd = ["worktree", "add", "--detach", str(worktree_dir)]
    if base_branch:
        cmd.append(base_branch)

    try:
        _run_git(cmd)
        runs_logger.info(f"Created worktree: {worktree_dir} (branch: {branch_name})")
    except RuntimeError as e:
        # If the worktree already exists (e.g., from a previous run), try to clean it
        if worktree_dir.exists():
            runs_logger.warning(f"Worktree directory already exists, removing: {worktree_dir}")
            try:
                remove_worktree(worktree_dir)
            except RuntimeError:
                pass
            # Retry creation
            _run_git(cmd)
        else:
            raise

    return WorktreeInfo(
        worktree_path=worktree_dir,
        branch_name=branch_name,
        project_path=project_path,
    )


def remove_worktree(worktree_path: Path) -> bool:
    """Remove a Git worktree and its associated branch.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        True if the worktree was removed, False if it didn't exist.

    Raises:
        RuntimeError: If Git commands fail.
    """
    if not worktree_path.exists():
        runs_logger.info(f"Worktree already removed: {worktree_path}")
        return False

    try:
        # Try to remove the worktree properly first
        _run_git(["worktree", "remove", "--force", str(worktree_path)])
        runs_logger.info(f"Removed worktree: {worktree_path}")
    except RuntimeError as e:
        runs_logger.warning(f"Failed to remove worktree via git: {e}")
        # Fallback: manually remove the directory
        import shutil
        try:
            shutil.rmtree(worktree_path)
            runs_logger.info(f"Manually removed worktree directory: {worktree_path}")
        except Exception as cleanup_err:
            runs_logger.error(f"Failed to manually remove worktree: {cleanup_err}")
            raise RuntimeError(f"Failed to remove worktree: {worktree_path}") from cleanup_err

    return True


def cleanup_orphaned_worktrees(
    project_path: Path,
    base_dir: Optional[Path] = None,
) -> List[Path]:
    """Find and remove leftover worktrees from crashed runs.

    Scans for worktrees under the `.junie-worktrees` directory that are no
    longer tracked by Git and removes them.

    Args:
        project_path: Path to the Git repository.
        base_dir: Optional base directory to scan. Defaults to the project's
            parent `.junie-worktrees` directory.

    Returns:
        List of paths that were cleaned up.
    """
    if not validate_git_repo(project_path):
        runs_logger.warning(f"Cannot clean up worktrees: {project_path} is not a Git repo.")
        return []

    git_root = _get_git_root(project_path)
    if base_dir is None:
        base_dir = git_root.parent / ".junie-worktrees"

    if not base_dir.exists():
        return []

    cleaned: List[Path] = []

    # Get list of all tracked worktrees
    try:
        tracked_output = _run_git(["worktree", "list"], cwd=project_path)
        tracked_paths = {
            Path(line.split()[0]).resolve()
            for line in tracked_output.splitlines()
            if line.strip()
        }
    except RuntimeError:
        tracked_paths = set()

    # Find orphaned worktree directories
    for item in base_dir.iterdir():
        if item.is_dir():
            resolved = item.resolve()
            if resolved not in tracked_paths:
                try:
                    # Check if it looks like a Junie worktree (has .git file pointing to main repo)
                    git_file = item / ".git"
                    if git_file.exists():
                        content = git_file.read_text(encoding="utf-8").strip()
                        if str(git_root) in content or "gitdir" in content:
                            runs_logger.info(f"Cleaning orphaned worktree: {item}")
                            import shutil
                            shutil.rmtree(item)
                            # Also try to remove any associated branch
                            try:
                                _run_git(["worktree", "prune"], cwd=project_path)
                            except RuntimeError:
                                pass
                            cleaned.append(item)
                except Exception as e:
                    runs_logger.warning(f"Failed to clean orphaned worktree {item}: {e}")

    return cleaned


def get_changed_files(worktree_path: Path) -> List[str]:
    """Get the list of changed files in a worktree.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        List of changed file paths relative to the worktree root.

    Raises:
        RuntimeError: If Git commands fail.
    """
    try:
        output = _run_git(["diff", "--name-only", "HEAD"], cwd=worktree_path)
        if not output:
            return []
        return [f for f in output.splitlines() if f.strip()]
    except RuntimeError:
        # If HEAD doesn't exist (no commits), try unstaged changes
        try:
            output = _run_git(["diff", "--name-only"], cwd=worktree_path)
            if not output:
                return []
            return [f for f in output.splitlines() if f.strip()]
        except RuntimeError:
            return []

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
    base_commit: str = ""


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


def _branch_exists(branch_name: str, project_path: Path) -> bool:
    """Check if a branch with the given name already exists."""
    try:
        output = _run_git(["branch", "--list", branch_name], cwd=project_path)
        return bool(output.strip())
    except RuntimeError:
        return False


def _make_unique_branch_name(worker_id: str, project_path: Path) -> str:
    """Generate a unique branch name, handling collisions.

    Appends a counter suffix if the base name already exists.
    """
    base = _make_branch_name(worker_id)
    branch_name = base
    counter = 1
    while _branch_exists(branch_name, project_path):
        branch_name = f"{base}-{counter}"
        counter += 1
    return branch_name


def _get_head_commit(project_path: Path) -> str:
    """Return the SHA of the current HEAD commit."""
    return _run_git(["rev-parse", "HEAD"], cwd=project_path)


def create_worktree(
    project_path: Path,
    worker_id: str,
    base_branch: Optional[str] = None,
) -> WorktreeInfo:
    """Create a Git worktree for an isolated worker execution.

    The worktree is created outside the main checkout with a unique branch
    name derived from the worker ID and timestamp. The branch is created
    and attached to the worktree (not detached HEAD).

    Args:
        project_path: Path to the Git repository.
        worker_id: Unique identifier for this worker.
        base_branch: Branch to base the worktree on. Defaults to the current HEAD.

    Returns:
        WorktreeInfo with the worktree path, branch name, project path, and base commit.

    Raises:
        RuntimeError: If the path is not a Git repo or Git commands fail.
    """
    if not validate_git_repo(project_path):
        raise RuntimeError(
            f"{project_path} is not a valid Git repository. "
            "Worktree isolation requires a Git repo."
        )

    branch_name = _make_unique_branch_name(worker_id, project_path)
    worktree_dir = _make_worktree_dir(project_path, branch_name)

    # Ensure the base worktrees directory exists
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    # Record the base commit before creating the worktree
    base_commit = _get_head_commit(project_path)

    # Build the git worktree add command with -b to create a new branch
    base_ref = base_branch if base_branch else "HEAD"
    cmd = ["worktree", "add", "-b", branch_name, str(worktree_dir), base_ref]

    try:
        _run_git(cmd, cwd=project_path)
        runs_logger.info(f"Created worktree: {worktree_dir} (branch: {branch_name})")
    except RuntimeError as e:
        # If the worktree already exists (e.g., from a previous run), try to clean it
        if worktree_dir.exists():
            runs_logger.warning(f"Worktree directory already exists, removing: {worktree_dir}")
            try:
                remove_worktree(worktree_dir)
            except RuntimeError:
                pass
            # Also clean up the branch if it exists
            try:
                cleanup_branch(branch_name, project_path)
            except RuntimeError:
                pass
            # Retry creation
            try:
                _run_git(cmd, cwd=project_path)
            except RuntimeError:
                raise RuntimeError(
                    f"Failed to create worktree after cleanup: {worktree_dir}"
                ) from e
        else:
            raise

    # Validate that Git tracks the worktree
    try:
        tracked = _run_git(["worktree", "list"], cwd=project_path)
        # Normalize paths for comparison (Git may use forward slashes on Windows)
        worktree_str = str(worktree_dir).replace("\\", "/")
        if worktree_str not in tracked:
            runs_logger.warning(
                f"Worktree created but not tracked by Git: {worktree_dir}. "
                "Attempting cleanup."
            )
            try:
                remove_worktree(worktree_dir)
            except RuntimeError:
                pass
            raise RuntimeError(f"Worktree validation failed: {worktree_dir}")
    except RuntimeError as validation_err:
        if "not tracked" in str(validation_err):
            raise
        runs_logger.warning(f"Could not validate worktree registration: {validation_err}")

    return WorktreeInfo(
        worktree_path=worktree_dir,
        branch_name=branch_name,
        project_path=project_path,
        base_commit=base_commit,
    )


def cleanup_branch(branch_name: str, project_path: Path) -> bool:
    """Safely remove a Git branch if it exists.

    Args:
        branch_name: Name of the branch to remove.
        project_path: Path to the Git repository.

    Returns:
        True if the branch was removed, False if it didn't exist.
    """
    if not _branch_exists(branch_name, project_path):
        return False
    try:
        _run_git(["branch", "-D", branch_name], cwd=project_path)
        runs_logger.info(f"Removed branch: {branch_name}")
        return True
    except RuntimeError as e:
        runs_logger.warning(f"Failed to remove branch {branch_name}: {e}")
        return False


def _is_junie_worktree_path(worktree_path: Path) -> bool:
    """Check if a path is inside a .junie-worktrees directory."""
    parts = worktree_path.parts
    return ".junie-worktrees" in parts


def remove_worktree(worktree_path: Path, branch_name: Optional[str] = None, project_path: Optional[Path] = None) -> bool:
    """Remove a Git worktree and its associated branch.

    Args:
        worktree_path: Path to the worktree directory.
        branch_name: Optional branch name to clean up after removing the worktree.
        project_path: Optional project path for branch cleanup.

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
        # Fallback: only manually remove if it's a known Junie worktree directory
        if _is_junie_worktree_path(worktree_path):
            import shutil
            try:
                shutil.rmtree(worktree_path)
                runs_logger.info(f"Manually removed worktree directory: {worktree_path}")
            except Exception as cleanup_err:
                runs_logger.error(f"Failed to manually remove worktree: {cleanup_err}")
                raise RuntimeError(f"Failed to remove worktree: {worktree_path}") from cleanup_err
        else:
            runs_logger.error(
                f"Refusing to delete non-Junie worktree directory: {worktree_path}. "
                "Manual cleanup required."
            )
            raise RuntimeError(f"Failed to remove worktree: {worktree_path}") from e

    # Clean up the associated branch
    if branch_name and project_path:
        cleanup_branch(branch_name, project_path)

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
        tracked_paths = set()
        for line in tracked_output.splitlines():
            if line.strip():
                # Git may use forward slashes on Windows; normalize
                wt_path_str = line.split()[0].replace("/", "\\")
                tracked_paths.add(Path(wt_path_str).resolve())
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


def _split_git_output(output: str) -> List[str]:
    """Split Git output, handling null-terminated and newline-separated formats."""
    if not output:
        return []
    # Handle null-terminated output (from -z flag)
    if "\x00" in output:
        return [f for f in output.split("\x00") if f.strip()]
    return [f for f in output.splitlines() if f.strip()]


def get_changed_files(worktree_path: Path) -> List[str]:
    """Get the list of all changed files in a worktree.

    Reports modified, added, deleted, renamed, staged, unstaged, and untracked files.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        List of changed file paths relative to the worktree root.

    Raises:
        RuntimeError: If Git commands fail.
    """
    changed = set()

    try:
        # Modified tracked files (unstaged changes)
        output = _run_git(["diff", "--name-only", "HEAD"], cwd=worktree_path)
        changed.update(_split_git_output(output))
    except RuntimeError:
        pass

    try:
        # Staged changes
        output = _run_git(["diff", "--cached", "--name-only"], cwd=worktree_path)
        changed.update(_split_git_output(output))
    except RuntimeError:
        pass

    try:
        # Untracked files
        output = _run_git(
            ["ls-files", "--others", "--exclude-standard"], cwd=worktree_path
        )
        changed.update(_split_git_output(output))
    except RuntimeError:
        pass

    try:
        # Added/deleted/renamed files (from name-status)
        output = _run_git(["diff", "--name-status", "HEAD"], cwd=worktree_path)
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                changed.add(parts[1])
            elif len(parts) == 1 and parts[0].strip():
                # Some edge cases
                changed.add(parts[0].strip())
    except RuntimeError:
        pass

    return sorted(changed)


@dataclass
class FileDiff:
    """Structured diff data for a single file."""
    file_path: str
    status: str  # M, A, D, R, U (Git status codes)
    diff_content: str
    summary: str


def get_worker_diff(worktree_path: Path, base_commit: Optional[str] = None) -> List[FileDiff]:
    """Get structured diff data for all changed files in a worktree.

    Args:
        worktree_path: Path to the worktree directory.
        base_commit: Optional base commit SHA to diff against. Defaults to HEAD.

    Returns:
        List of FileDiff objects with file path, status, diff content, and summary.
    """
    diffs = []
    changed_files = get_changed_files(worktree_path)

    for file_path in changed_files:
        try:
            # Determine status
            status = "M"  # default
            try:
                name_status_output = _run_git(
                    ["diff", "--name-status", "HEAD", "--", file_path],
                    cwd=worktree_path,
                )
                if name_status_output:
                    parts = name_status_output.split("\t")
                    if parts:
                        status = parts[0].strip()
            except RuntimeError:
                pass

            # Get diff content
            diff_content = ""
            try:
                diff_content = _run_git(
                    ["diff", "HEAD", "--", file_path],
                    cwd=worktree_path,
                )
            except RuntimeError:
                # File might be untracked - show content
                try:
                    diff_content = _run_git(
                        ["diff", "--no-index", "/dev/null", file_path],
                        cwd=worktree_path,
                    )
                except RuntimeError:
                    diff_content = "(diff unavailable)"

            # Create a short summary
            summary = f"{status}: {file_path}"
            if diff_content:
                lines = diff_content.strip().split("\n")
                added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
                removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
                summary = f"{status}: {file_path} (+{added}/-{removed})"

            diffs.append(FileDiff(
                file_path=file_path,
                status=status,
                diff_content=diff_content,
                summary=summary,
            ))
        except Exception as e:
            runs_logger.warning(f"Failed to get diff for {file_path}: {e}")
            diffs.append(FileDiff(
                file_path=file_path,
                status="?",
                diff_content=f"(error: {e})",
                summary=f"Error getting diff for {file_path}",
            ))

    return diffs


def get_change_summary(worktree_path: Path) -> str:
    """Get a human-readable summary of changes in a worktree.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        Human-readable summary string suitable for AgentHub reporting.
    """
    changed_files = get_changed_files(worktree_path)
    if not changed_files:
        return "No changes detected."

    # Count by status
    modified = 0
    added = 0
    deleted = 0
    renamed = 0
    untracked = 0

    for file_path in changed_files:
        try:
            status_output = _run_git(
                ["diff", "--name-status", "HEAD", "--", file_path],
                cwd=worktree_path,
            )
            if status_output:
                status = status_output.split("\t")[0].strip()
                if status.startswith("R"):
                    renamed += 1
                elif status == "A":
                    added += 1
                elif status == "D":
                    deleted += 1
                elif status == "M":
                    modified += 1
            else:
                # No diff against HEAD means it's untracked
                untracked += 1
        except RuntimeError:
            untracked += 1

    parts = []
    if modified:
        parts.append(f"{modified} modified")
    if added:
        parts.append(f"{added} added")
    if deleted:
        parts.append(f"{deleted} deleted")
    if renamed:
        parts.append(f"{renamed} renamed")
    if untracked:
        parts.append(f"{untracked} untracked")

    summary = f"Changes: {', '.join(parts)} ({len(changed_files)} files total)"
    return summary

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


def _is_junie_worktree_path(worktree_path: Path, git_root: Optional[Path] = None) -> bool:
    """Check if a resolved path is inside the correct .junie-worktrees directory.

    Validates that the path is not only named `.junie-worktrees` but actually
    lives next to the expected Git repository root. Resolves symlinks to prevent
    escape attacks.

    Args:
        worktree_path: Path to check (will be resolved).
        git_root: Expected Git repository root. If provided, verifies the
            `.junie-worktrees` directory is directly under this root's parent.

    Returns:
        True if the path is a legitimate Junie worktree path.
    """
    resolved = worktree_path.resolve()

    # Check that .junie-worktrees is in the resolved path components
    if ".junie-worktrees" not in resolved.parts:
        return False

    # If git_root is provided, verify the .junie-worktrees directory is
    # actually the one belonging to that repo (not a同名 directory elsewhere)
    if git_root is not None:
        expected_base = git_root.resolve().parent / ".junie-worktrees"
        try:
            resolved.relative_to(expected_base.resolve())
        except ValueError:
            # Not under the expected .junie-worktrees directory
            return False

    return True


def _is_worktree_dirty(worktree_path: Path) -> bool:
    """Check if a worktree has uncommitted changes (modified, staged, untracked, or ignored).

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        True if the worktree has any uncommitted changes, including ignored files.

    Raises:
        RuntimeError: If Git status command fails and the dirty state cannot
            be determined. The caller must decide whether to preserve or delete.
    """
    # Check for modified, staged, or untracked files. If this fails, propagate
    # the error so the caller knows the state is undetermined.
    status_output = _run_git(["status", "--porcelain"], cwd=worktree_path)
    if status_output.strip():
        return True

    # git status --porcelain excludes ignored files by default. A worktree
    # containing only ignored files (e.g., .env, __pycache__/, .DS_Store)
    # would appear clean above. Check for ignored files to prevent data loss.
    try:
        ignored_output = _run_git(
            ["status", "--porcelain", "--ignored"], cwd=worktree_path
        )
        # --ignored output includes both untracked (??) and ignored (!!) entries.
        # Filter out entries that are inside .git/ itself (Git internals).
        for line in ignored_output.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Skip Git's own internal files
            if line.startswith("!! .git/"):
                continue
            # Any remaining line (?? or !!) means there are local files
            return True
    except RuntimeError:
        # If the ignored check fails, assume dirty to be safe
        return True

    return False


def remove_worktree(
    worktree_path: Path,
    branch_name: Optional[str] = None,
    project_path: Optional[Path] = None,
    force: bool = False,
) -> bool:
    """Remove a Git worktree and its associated branch.

    Resolves and validates paths before deleting anything. Restricts manual
    directory deletion to confirmed Junie worktrees inside the designated
    `.junie-worktrees` directory. Never deletes the main checkout or an
    unrelated directory. Refuses to delete a dirty worktree (one with
    uncommitted changes) unless ``force=True`` is explicitly passed.

    Args:
        worktree_path: Path to the worktree directory.
        branch_name: Optional branch name to clean up after removing the worktree.
        project_path: Optional project path for branch cleanup and ownership
            verification.
        force: If True, allow deletion even when the worktree contains
            uncommitted changes. Defaults to False.

    Returns:
        True if the worktree was removed, False if it didn't exist.

    Raises:
        RuntimeError: If Git commands fail, the path is not a safe Junie
            worktree, or the worktree contains unreviewed changes and
            ``force`` is False.
    """
    resolved = worktree_path.resolve()

    if not resolved.exists():
        runs_logger.info(f"Worktree already removed: {resolved}")
        return False

    # Determine the git_root for ownership checks
    git_root_for_check: Optional[Path] = None
    if project_path is not None:
        try:
            git_root_for_check = _get_git_root(project_path).resolve()
        except RuntimeError:
            pass

    # Safety check: never delete the main checkout
    if not _is_junie_worktree_path(resolved, git_root_for_check):
        try:
            git_root = _get_git_root(resolved)
            if resolved == git_root:
                runs_logger.error(
                    f"Refusing to delete what appears to be the main Git checkout: {resolved}"
                )
                raise RuntimeError(
                    f"Refusing to delete main Git checkout: {resolved}. "
                    "This is not a Junie worktree."
                )
        except RuntimeError as e:
            if "main Git checkout" in str(e):
                raise
            pass

    # Check for unreviewed changes before allowing removal.
    # Only check if this looks like a valid Git worktree (has .git file).
    # If there's no .git file, the Git command will fail and we'll handle
    # that in the removal section below.
    if not force:
        git_file = resolved / ".git"
        if git_file.exists():
            try:
                if _is_worktree_dirty(resolved):
                    runs_logger.warning(
                        f"Refusing to remove dirty worktree: {resolved}. "
                        "Use force=True to override."
                    )
                    raise RuntimeError(
                        f"Worktree {resolved} contains uncommitted changes. "
                        "Commit or discard changes first, or pass force=True."
                    )
            except RuntimeError as e:
                if "uncommitted changes" in str(e):
                    raise
                # If we can't check dirty state on a valid worktree,
                # assume dirty and refuse
                runs_logger.warning(
                    f"Cannot check worktree dirty state, refusing removal: {resolved}"
                )
                raise RuntimeError(
                    f"Cannot verify worktree {resolved} is clean. "
                    "Manual inspection required."
                ) from e
        # No .git file — skip dirty check; the removal logic below will
        # handle the missing .git file case.

    try:
        # Try to remove the worktree properly
        _run_git(["worktree", "remove", str(resolved)])
        runs_logger.info(f"Removed worktree: {resolved}")
    except RuntimeError as e:
        runs_logger.warning(f"Failed to remove worktree via git: {e}")
        # Fallback: only manually remove if it's a confirmed Junie worktree
        # with verified ownership
        if _is_junie_worktree_path(resolved, git_root_for_check):
            # Verify ownership before any manual deletion
            if git_root_for_check is not None and not _verify_worktree_ownership(
                resolved, git_root_for_check
            ):
                runs_logger.error(
                    f"Refusing to delete {resolved}: ownership verification failed. "
                    "Manual cleanup required."
                )
                raise RuntimeError(
                    f"Refusing to delete {resolved}: not owned by the expected repo. "
                    "Manual cleanup required."
                )

            git_file = resolved / ".git"
            if git_file.exists():
                import shutil
                try:
                    shutil.rmtree(resolved)
                    runs_logger.info(f"Manually removed worktree directory: {resolved}")
                except Exception as cleanup_err:
                    runs_logger.error(f"Failed to manually remove worktree: {cleanup_err}")
                    raise RuntimeError(
                        f"Failed to remove worktree: {resolved}"
                    ) from cleanup_err
            else:
                runs_logger.error(
                    f"Refusing to delete Junie worktree path without .git file: {resolved}. "
                    "Manual cleanup required."
                )
                raise RuntimeError(
                    f"Refusing to delete {resolved}: missing .git file. "
                    "Manual cleanup required."
                )
        else:
            runs_logger.error(
                f"Refusing to delete non-Junie worktree directory: {resolved}. "
                "Manual cleanup required."
            )
            raise RuntimeError(f"Failed to remove worktree: {resolved}") from e

    # Clean up the associated branch
    if branch_name and project_path:
        cleanup_branch(branch_name, project_path)

    return True


def _verify_worktree_ownership(item: Path, git_root: Path) -> bool:
    """Verify that a directory is a worktree belonging to the given Git repo.

    Uses canonical path resolution to confirm the worktree's Git metadata
    points to the main repository. Does not rely on substring matching that
    could be fooled by forged paths (e.g., `/my-repo-backup/.git` matching
    `/my-repo/.git`).

    Args:
        item: Path to the potential worktree directory.
        git_root: Path to the main Git repository root (will be resolved).

    Returns:
        True if the directory is confirmed as a worktree of the given repo.
    """
    git_file = item / ".git"
    if not git_file.exists():
        return False

    try:
        content = git_file.read_text(encoding="utf-8").strip()
    except Exception:
        return False

    # Resolve git_root to its canonical form
    git_root_resolved = git_root.resolve()
    expected_git_dir = (git_root_resolved / ".git").resolve()

    # A worktree .git file contains "gitdir: <path>" pointing to
    # .git/worktrees/<branch> in the main repo's .git directory.
    # Extract the path from the gitdir: directive.
    if not content.startswith("gitdir:"):
        return False

    gitdir_value = content[len("gitdir:"):].strip()

    # Resolve the gitdir path to its canonical form
    # It may be relative (to the worktree) or absolute
    try:
        gitdir_path = Path(gitdir_value)
        if not gitdir_path.is_absolute():
            gitdir_path = (item / gitdir_path).resolve()
        else:
            gitdir_path = gitdir_path.resolve()
    except Exception:
        return False

    # The gitdir path should be under the expected .git directory
    # of the main repository. Typically it is:
    # <git_root>/.git/worktrees/<branch_name>
    try:
        gitdir_path.relative_to(expected_git_dir)
        return True
    except ValueError:
        # Also check if gitdir points directly to the .git directory
        try:
            gitdir_path.relative_to(git_root_resolved / ".git")
            return True
        except ValueError:
            return False


def cleanup_orphaned_worktrees(
    project_path: Path,
    base_dir: Optional[Path] = None,
) -> List[Path]:
    """Find and remove leftover worktrees from crashed runs.

    Scans for worktrees under the `.junie-worktrees` directory that are no
    longer tracked by Git and removes them. Only deletes directories that
    can be positively identified as Junie worktrees belonging to the given
    repository. Never deletes the main checkout or unrelated directories.

    If ``git worktree list`` or another essential safety check fails, aborts
    with a RuntimeError instead of assuming there are no tracked worktrees.

    Args:
        project_path: Path to the Git repository.
        base_dir: Optional base directory to scan. Defaults to the project's
            parent `.junie-worktrees` directory.

    Returns:
        List of paths that were cleaned up.

    Raises:
        RuntimeError: If essential Git commands fail (e.g., ``git worktree list``),
            because the safety of cleanup cannot be verified.
    """
    if not validate_git_repo(project_path):
        runs_logger.warning(f"Cannot clean up worktrees: {project_path} is not a Git repo.")
        return []

    git_root = _get_git_root(project_path).resolve()
    if base_dir is None:
        base_dir = git_root.parent / ".junie-worktrees"

    base_dir = base_dir.resolve()

    if not base_dir.exists():
        return []

    # Safety: ensure base_dir is actually a .junie-worktrees directory
    # belonging to this specific repo, not just any directory with that name
    expected_base = git_root.parent / ".junie-worktrees"
    if base_dir != expected_base.resolve():
        runs_logger.error(
            f"Refusing to scan non-Junie directory for orphaned worktrees: {base_dir}. "
            f"Expected: {expected_base}"
        )
        return []

    cleaned: List[Path] = []

    # Get list of all tracked worktrees — essential safety check.
    # If this fails, we cannot safely determine what is orphaned, so abort.
    try:
        tracked_output = _run_git(["worktree", "list"], cwd=project_path)
    except RuntimeError as e:
        runs_logger.error(
            f"Cannot list tracked worktrees — aborting cleanup: {e}. "
            "Manual inspection required."
        )
        raise RuntimeError(
            f"Cannot verify tracked worktrees for {project_path}. "
            "Git worktree list failed — aborting cleanup."
        ) from e

    tracked_paths = set()
    for line in tracked_output.splitlines():
        if line.strip():
            # Git may use forward slashes on Windows; normalize
            wt_path_str = line.split()[0].replace("/", "\\")
            tracked_paths.add(Path(wt_path_str).resolve())

    # Find orphaned worktree directories
    for item in base_dir.iterdir():
        if not item.is_dir():
            continue

        resolved = item.resolve()

        # Never delete a tracked worktree
        if resolved in tracked_paths:
            continue

        # Verify ownership before deletion — must use canonical path resolution
        if not _verify_worktree_ownership(item, git_root):
            runs_logger.info(
                f"Skipping unowned directory during orphan cleanup: {item}"
            )
            continue

        # Safety check: the directory must have a .git file to be classified
        # as a Junie worktree. Without it, we cannot establish the relationship
        # to Git — preserve for manual inspection.
        git_file_for_check = item / ".git"
        if not git_file_for_check.exists():
            runs_logger.warning(
                f"Skipping orphaned directory without .git file — "
                f"cannot establish Git relationship: {item}. "
                "Preserving for manual inspection."
            )
            continue

        # Read the .git file to determine the worktree's state
        try:
            content = git_file_for_check.read_text(encoding="utf-8").strip()
        except Exception as e:
            runs_logger.warning(
                f"Cannot read .git file for orphaned directory — "
                f"preserving for manual inspection: {item}. Error: {e}"
            )
            continue

        # A legitimate worktree .git file contains "gitdir: <path>".
        # Without this directive, we cannot confirm this is a worktree.
        if not content.startswith("gitdir:"):
            runs_logger.warning(
                f"Skipping orphaned directory with unrecognized .git format: {item}. "
                "Preserving for manual inspection."
            )
            continue

        # Extract and resolve the gitdir path
        gitdir_value = content[len("gitdir:"):].strip()
        try:
            gitdir_path = Path(gitdir_value)
            if not gitdir_path.is_absolute():
                gitdir_path = (item / gitdir_path).resolve()
            else:
                gitdir_path = gitdir_path.resolve()
        except Exception as e:
            runs_logger.warning(
                f"Cannot resolve gitdir path for orphaned directory — "
                f"preserving for manual inspection: {item}. Error: {e}"
            )
            continue

        # If the gitdir metadata no longer exists, the worktree is truly orphaned.
        # git status will fail on such worktrees because the gitfile points to
        # a non-existent path. We must handle this case separately.
        gitdir_missing = not gitdir_path.exists()
        if gitdir_missing:
            runs_logger.info(
                f"Orphaned worktree has stale metadata: {item}. "
                "Checking contents before cleanup."
            )

        # Check for unreviewed changes. This is the critical safety gate:
        # if we cannot determine the dirty state, we preserve the directory.
        # However, if the gitdir metadata is already known to be missing,
        # git status will inevitably fail — in that case, check for untracked
        # files manually instead of relying on git status.
        dirty = False
        if gitdir_missing:
            # git status will fail on stale worktrees. Check for untracked
            # files by scanning the directory for non-.git files.
            try:
                has_files = any(
                    f.name != ".git" for f in item.iterdir()
                )
                if has_files:
                    # The orphaned worktree contains files beyond .git.
                    # These could be unreviewed changes — preserve the directory.
                    runs_logger.warning(
                        f"Skipping orphaned worktree with files — "
                        f"preserving for manual inspection: {item}"
                    )
                    continue
            except Exception as e:
                runs_logger.warning(
                    f"Cannot scan orphaned worktree directory — "
                    f"preserving for manual inspection: {item}. Error: {e}"
                )
                continue
            # No files beyond .git — safe to clean
        else:
            # gitdir metadata exists — git status should work
            try:
                dirty = _is_worktree_dirty(item)
            except RuntimeError as e:
                # _is_worktree_dirty() raised — git status failed even though
                # gitdir metadata exists. We cannot safely determine the state.
                runs_logger.warning(
                    f"Cannot determine dirty state of orphaned worktree — "
                    f"preserving for manual inspection: {item}. "
                    f"Git status failed: {e}"
                )
                continue
            except Exception as e:
                runs_logger.warning(
                    f"Unexpected error checking orphaned worktree — "
                    f"preserving for manual inspection: {item}. Error: {e}"
                )
                continue

            if dirty:
                runs_logger.warning(
                    f"Skipping orphaned worktree with unreviewed changes: {item}"
                )
                continue

        # All safety checks passed:
        # 1. Directory is inside the correct .junie-worktrees base
        # 2. Ownership verified via _verify_worktree_ownership
        # 3. .git file exists and contains valid gitdir: directive
        # 4. Dirty state is known and worktree is clean
        # 5. Directory is not tracked by Git (orphaned)
        try:
            runs_logger.info(f"Cleaning orphaned worktree: {item}")
            import shutil
            shutil.rmtree(item)
            cleaned.append(item)
        except Exception as e:
            runs_logger.warning(f"Failed to clean orphaned worktree {item}: {e}")

    # Prune Git's internal worktree tracking
    try:
        _run_git(["worktree", "prune"], cwd=project_path)
    except RuntimeError:
        pass

    return cleaned


def _split_git_output(output: str) -> List[str]:
    """Split Git output, handling null-terminated and newline-separated formats."""
    if not output:
        return []
    # Handle null-terminated output (from -z flag)
    if "\x00" in output:
        return [f for f in output.split("\x00") if f.strip()]
    return [f for f in output.splitlines() if f.strip()]


def get_changed_files(worktree_path: Path, base_commit: Optional[str] = None) -> List[str]:
    """Get the list of all changed files in a worktree.

    Reports modified, added, deleted, renamed, staged, unstaged, and untracked files.
    Distinguishes a genuinely clean worktree from a failed Git inspection: if essential
    Git commands fail, a RuntimeError is raised rather than returning an empty list.

    Args:
        worktree_path: Path to the worktree directory.
        base_commit: Optional base commit SHA to diff against. When provided, compares
            the worktree state against this commit instead of HEAD, so that files
            committed by the worker after the base commit are included in changes.

    Returns:
        List of changed file paths relative to the worktree root.

    Raises:
        RuntimeError: If the path is not a Git repo or essential Git commands fail.
    """
    if not worktree_path.exists() or not worktree_path.is_dir():
        raise RuntimeError(f"Worktree path does not exist: {worktree_path}")

    if not validate_git_repo(worktree_path):
        raise RuntimeError(f"Worktree path is not a Git repository: {worktree_path}")

    changed = set()
    diff_ref = base_commit if base_commit else "HEAD"
    essential_failed = False

    # Modified tracked files (unstaged changes) — essential check
    try:
        output = _run_git(["diff", "--name-only", diff_ref], cwd=worktree_path)
        changed.update(_split_git_output(output))
    except RuntimeError as e:
        runs_logger.error(f"Essential Git diff failed: {e}")
        essential_failed = True

    # Staged changes
    try:
        output = _run_git(["diff", "--cached", "--name-only"], cwd=worktree_path)
        changed.update(_split_git_output(output))
    except RuntimeError as e:
        runs_logger.error(f"Staged diff failed: {e}")
        essential_failed = True

    # Untracked files
    try:
        output = _run_git(
            ["ls-files", "--others", "--exclude-standard"], cwd=worktree_path
        )
        changed.update(_split_git_output(output))
    except RuntimeError as e:
        runs_logger.error(f"Untracked files check failed: {e}")
        essential_failed = True

    # Added/deleted/renamed files (from name-status)
    try:
        output = _run_git(["diff", "--name-status", diff_ref], cwd=worktree_path)
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                changed.add(parts[1])
            elif len(parts) == 1 and parts[0].strip():
                # Some edge cases
                changed.add(parts[0].strip())
    except RuntimeError as e:
        runs_logger.error(f"Name-status diff failed: {e}")
        essential_failed = True

    if essential_failed:
        raise RuntimeError(
            f"Git inspection failed for worktree {worktree_path}. "
            "One or more essential Git commands could not complete. "
            "The worktree may be corrupted or Git may be unavailable."
        )

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
        base_commit: Optional base commit SHA to diff against. When provided, compares
            the worktree state against this commit instead of HEAD, so that files
            committed by the worker after the base commit are included.

    Returns:
        List of FileDiff objects with file path, status, diff content, and summary.

    Raises:
        RuntimeError: If Git commands fail to inspect the worktree.
    """
    diffs = []
    changed_files = get_changed_files(worktree_path, base_commit=base_commit)

    diff_ref = base_commit if base_commit else "HEAD"

    for file_path in changed_files:
        try:
            # Determine status
            status = "M"  # default
            is_untracked_file = False
            try:
                name_status_output = _run_git(
                    ["diff", "--name-status", diff_ref, "--", file_path],
                    cwd=worktree_path,
                )
                if name_status_output:
                    parts = name_status_output.split("\t")
                    if parts:
                        status = parts[0].strip()
            except RuntimeError:
                # File may be untracked — no diff against HEAD/base_commit
                pass

            # Check if the file is untracked by seeing if it's in the index
            file_full_path = worktree_path / file_path
            if file_full_path.exists():
                try:
                    # Check if the file is tracked (in the index)
                    _run_git(["ls-files", "--error-unmatch", file_path], cwd=worktree_path)
                    # File is tracked — status from diff is correct
                except RuntimeError:
                    # File is not in the index — it's untracked
                    is_untracked_file = True
                    status = "U"

            # Get diff content
            diff_content = ""
            if is_untracked_file and file_full_path.exists():
                # Untracked file — show content as diff (skip git diff which returns empty)
                try:
                    content = file_full_path.read_text(encoding="utf-8", errors="replace")
                    # Format as a unified diff against empty
                    lines = content.split("\n")
                    diff_lines = [f"+++ b/{file_path}"]
                    for line in lines:
                        diff_lines.append(f"+{line}")
                    diff_content = "\n".join(diff_lines)
                except Exception:
                    diff_content = "(could not read file content)"
            else:
                try:
                    diff_content = _run_git(
                        ["diff", diff_ref, "--", file_path],
                        cwd=worktree_path,
                    )
                    # If diff is empty and file doesn't exist, it was deleted
                    if not diff_content and not file_full_path.exists():
                        if status != "D":
                            status = "D"
                        diff_content = f"(deleted: {file_path})"
                except RuntimeError:
                    # File might be deleted
                    if not file_full_path.exists():
                        if status != "D":
                            status = "D"
                        diff_content = f"(deleted: {file_path})"

            # Create a short summary
            summary = f"{status}: {file_path}"
            if diff_content and not diff_content.startswith("("):
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


def get_change_summary(worktree_path: Path, base_commit: Optional[str] = None) -> str:
    """Get a human-readable summary of changes in a worktree.

    Args:
        worktree_path: Path to the worktree directory.
        base_commit: Optional base commit SHA to diff against. When provided, compares
            the worktree state against this commit instead of HEAD.

    Returns:
        Human-readable summary string suitable for AgentHub reporting.

    Raises:
        RuntimeError: If Git commands fail to inspect the worktree.
    """
    changed_files = get_changed_files(worktree_path, base_commit=base_commit)
    if not changed_files:
        return "No changes detected."

    diff_ref = base_commit if base_commit else "HEAD"

    # Count by status
    modified = 0
    added = 0
    deleted = 0
    renamed = 0
    untracked = 0

    for file_path in changed_files:
        try:
            status_output = _run_git(
                ["diff", "--name-status", diff_ref, "--", file_path],
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
            # No diff output means untracked
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

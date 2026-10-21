"""Persistent SQLite storage layer for Slinker.

Provides a versioned schema for storing projects, tasks, workers, sessions,
and execution metadata. Supports concurrent access, transactions, and
interrupted writes. Obsidian remains optional.
"""

import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# Schema version for migration support
SCHEMA_VERSION = 1


@dataclass
class ProjectRecord:
    """Stored project configuration."""

    id: str
    name: str
    path: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class TaskRecord:
    """Stored task execution record."""

    id: str
    project_id: str | None
    worker_id: str
    parent_task_id: str | None
    task_description: str
    project_path: str
    worktree_path: str | None
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    result_summary: str | None
    changed_files: str  # JSON-encoded list
    error: str | None
    duration_seconds: float
    start_time: datetime
    end_time: datetime | None


class PersistentStore:
    """Thread-safe SQLite storage for Slinker task and session persistence.

    Uses WAL mode for concurrent read/write access. Handles schema
    initialization and versioned migrations.
    """

    def __init__(self, db_path: str = ":memory:"):
        """Initialize the persistent store.

        Args:
            db_path: Path to the SQLite database file. Use ":memory:" for
                in-memory storage (testing). Defaults to in-memory.
        """
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local database connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        """Initialize the database schema."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Create version table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                migrated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            )
        """)

        # Check current version
        cursor.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        current_version = cursor.fetchone()[0]

        # Migrate to latest version
        if current_version < 1:
            self._migrate_v1(cursor)
            cursor.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (1)")
            conn.commit()
            logger.info(f"Database schema initialized to version {SCHEMA_VERSION}")

    def _migrate_v1(self, cursor: sqlite3.Cursor):
        """Create the initial schema (version 1)."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                worker_id TEXT NOT NULL,
                parent_task_id TEXT,
                task_description TEXT NOT NULL,
                project_path TEXT NOT NULL,
                worktree_path TEXT,
                status TEXT NOT NULL,
                exit_code INTEGER,
                stdout TEXT DEFAULT '',
                stderr TEXT DEFAULT '',
                result_summary TEXT,
                changed_files TEXT DEFAULT '[]',
                error TEXT,
                duration_seconds REAL DEFAULT 0.0,
                start_time TEXT NOT NULL,
                end_time TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                raw_prompt TEXT NOT NULL,
                refined_prompt TEXT,
                answer TEXT DEFAULT '',
                status TEXT NOT NULL,
                error TEXT,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                project_path TEXT NOT NULL,
                worktree_path TEXT,
                status TEXT NOT NULL,
                pid INTEGER,
                start_time TEXT NOT NULL,
                end_time TEXT,
                exit_code INTEGER,
                error TEXT
            )
        """)

        # Indexes for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_worker_id ON tasks(worker_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_task_id ON sessions(task_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_workers_task_id ON workers(task_id)"
        )

    def upsert_project(self, project_id: str, name: str, path: str) -> ProjectRecord:
        """Insert or update a project record.

        Args:
            project_id: Unique project identifier.
            name: Human-readable project name.
            path: Absolute path to the project directory.

        Returns:
            The ProjectRecord that was inserted or updated.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO projects (id, name, path) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                path = excluded.path,
                updated_at = strftime('%Y-%m-%dT%H:%M:%S', 'now')
        """,
            (project_id, name, path),
        )
        conn.commit()
        return ProjectRecord(id=project_id, name=name, path=path)

    def get_project(self, project_id: str) -> ProjectRecord | None:
        """Get a project by ID.

        Args:
            project_id: Unique project identifier.

        Returns:
            ProjectRecord if found, None otherwise.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return ProjectRecord(
            id=row["id"],
            name=row["name"],
            path=row["path"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_projects(self) -> list[ProjectRecord]:
        """List all registered projects.

        Returns:
            List of ProjectRecord objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM projects ORDER BY updated_at DESC")
        return [
            ProjectRecord(
                id=row["id"],
                name=row["name"],
                path=row["path"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in cursor.fetchall()
        ]

    def save_task(self, task: TaskRecord) -> TaskRecord:
        """Insert or update a task record.

        Args:
            task: The TaskRecord to save.

        Returns:
            The saved TaskRecord.
        """
        import json

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tasks (
                id, project_id, worker_id, parent_task_id, task_description,
                project_path, worktree_path, status, exit_code, stdout, stderr,
                result_summary, changed_files, error, duration_seconds,
                start_time, end_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                exit_code = excluded.exit_code,
                stdout = excluded.stdout,
                stderr = excluded.stderr,
                result_summary = excluded.result_summary,
                changed_files = excluded.changed_files,
                error = excluded.error,
                duration_seconds = excluded.duration_seconds,
                end_time = excluded.end_time
        """,
            (
                task.id,
                task.project_id,
                task.worker_id,
                task.parent_task_id,
                task.task_description,
                task.project_path,
                task.worktree_path,
                task.status,
                task.exit_code,
                task.stdout,
                task.stderr,
                task.result_summary,
                json.dumps(task.changed_files)
                if isinstance(task.changed_files, list)
                else task.changed_files,
                task.error,
                task.duration_seconds,
                task.start_time.isoformat(),
                task.end_time.isoformat() if task.end_time else None,
            ),
        )
        conn.commit()
        return task

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Get a task by ID.

        Args:
            task_id: Unique task identifier.

        Returns:
            TaskRecord if found, None otherwise.
        """
        import json

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return TaskRecord(
            id=row["id"],
            project_id=row["project_id"],
            worker_id=row["worker_id"],
            parent_task_id=row["parent_task_id"],
            task_description=row["task_description"],
            project_path=row["project_path"],
            worktree_path=row["worktree_path"],
            status=row["status"],
            exit_code=row["exit_code"],
            stdout=row["stdout"] or "",
            stderr=row["stderr"] or "",
            result_summary=row["result_summary"],
            changed_files=json.loads(row["changed_files"])
            if row["changed_files"]
            else [],
            error=row["error"],
            duration_seconds=row["duration_seconds"] or 0.0,
            start_time=datetime.fromisoformat(row["start_time"]),
            end_time=datetime.fromisoformat(row["end_time"])
            if row["end_time"]
            else None,
        )

    def list_tasks(
        self,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskRecord]:
        """List tasks with optional filtering.

        Args:
            project_id: Filter by project ID.
            status: Filter by status.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of TaskRecord objects.
        """
        import json

        conn = self._get_connection()
        cursor = conn.cursor()
        query = "SELECT * FROM tasks WHERE 1=1"
        params: list = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY start_time DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        return [
            TaskRecord(
                id=row["id"],
                project_id=row["project_id"],
                worker_id=row["worker_id"],
                parent_task_id=row["parent_task_id"],
                task_description=row["task_description"],
                project_path=row["project_path"],
                worktree_path=row["worktree_path"],
                status=row["status"],
                exit_code=row["exit_code"],
                stdout=row["stdout"] or "",
                stderr=row["stderr"] or "",
                result_summary=row["result_summary"],
                changed_files=json.loads(row["changed_files"])
                if row["changed_files"]
                else [],
                error=row["error"],
                duration_seconds=row["duration_seconds"] or 0.0,
                start_time=datetime.fromisoformat(row["start_time"]),
                end_time=datetime.fromisoformat(row["end_time"])
                if row["end_time"]
                else None,
            )
            for row in cursor.fetchall()
        ]

    def save_session(
        self,
        session_id: str,
        task_id: str | None,
        raw_prompt: str,
        refined_prompt: str | None,
        answer: str,
        status: str,
        error: str | None = None,
    ) -> str:
        """Save a session log record.

        Args:
            session_id: Unique session identifier.
            task_id: Associated task ID.
            raw_prompt: Original prompt.
            refined_prompt: Refined prompt (if applicable).
            answer: Response content.
            status: Session status.
            error: Error message if applicable.

        Returns:
            The session ID.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO sessions (
                id, task_id, raw_prompt, refined_prompt, answer, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (session_id, task_id, raw_prompt, refined_prompt, answer, status, error),
        )
        conn.commit()
        return session_id

    def list_sessions(
        self,
        task_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List session records.

        Args:
            task_id: Filter by task ID.
            limit: Maximum number of results.

        Returns:
            List of session dictionaries.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        query = "SELECT * FROM sessions WHERE 1=1"
        params: list = []

        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def update_task_status(
        self, task_id: str, status: str, error: str | None = None
    ) -> bool:
        """Update a task's status.

        Args:
            task_id: Task identifier.
            status: New status value.
            error: Optional error message.

        Returns:
            True if the task was found and updated.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        if error:
            cursor.execute(
                "UPDATE tasks SET status = ?, error = ? WHERE id = ?",
                (status, error, task_id),
            )
        else:
            cursor.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                (status, task_id),
            )
        conn.commit()
        return cursor.rowcount > 0

    def close(self):
        """Close the database connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

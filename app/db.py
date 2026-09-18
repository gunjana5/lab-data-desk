# datasets / tags / jobs / results - routes stay thin

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    owner TEXT NOT NULL,
    file_path TEXT NOT NULL,
    schema_notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_tags (
    dataset_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (dataset_id, tag),
    FOREIGN KEY (dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_message TEXT,
    FOREIGN KEY (dataset_id) REFERENCES datasets(id)
);

CREATE TABLE IF NOT EXISTS job_results (
    job_id INTEGER PRIMARY KEY,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_dataset_tags_tag ON dataset_tags(tag);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    # always store utc iso - easier than naive local stamps later
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


@dataclass
class Dataset:
    id: int
    name: str
    owner: str
    file_path: str
    schema_notes: Optional[str]
    created_at: str
    tags: list[str]


@dataclass
class Job:
    id: int
    dataset_id: int
    status: str
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    error_message: Optional[str]
    result: Optional[dict[str, Any]] = None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # check_same_thread=False - api + worker / tests may share the file
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # wal - api + worker hit the same file
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def create_dataset(
        self,
        *,
        name: str,
        owner: str,
        file_path: str,
        schema_notes: Optional[str],
        tags: list[str],
    ) -> Dataset:
        created = to_iso(utc_now())
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO datasets (name, owner, file_path, schema_notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, owner, file_path, schema_notes or "", created),
            )
            dataset_id = int(cur.lastrowid)
            for tag in tags:
                clean = tag.strip()
                if not clean:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO dataset_tags (dataset_id, tag) VALUES (?, ?)",
                    (dataset_id, clean),
                )
        return self.get_dataset(dataset_id)  # type: ignore[return-value]

    def _tags_for(self, conn: sqlite3.Connection, dataset_id: int) -> list[str]:
        rows = conn.execute(
            "SELECT tag FROM dataset_tags WHERE dataset_id = ? ORDER BY tag",
            (dataset_id,),
        ).fetchall()
        return [str(r["tag"]) for r in rows]

    def _row_to_dataset(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Dataset:
        return Dataset(
            id=int(row["id"]),
            name=str(row["name"]),
            owner=str(row["owner"]),
            file_path=str(row["file_path"]),
            schema_notes=row["schema_notes"],
            created_at=str(row["created_at"]),
            tags=self._tags_for(conn, int(row["id"])),
        )

    def list_datasets(self, *, tag: Optional[str] = None) -> list[Dataset]:
        with self.connect() as conn:
            if tag:
                rows = conn.execute(
                    """
                    SELECT d.* FROM datasets d
                    JOIN dataset_tags t ON t.dataset_id = d.id
                    WHERE t.tag = ?
                    ORDER BY d.id DESC
                    """,
                    (tag.strip(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM datasets ORDER BY id DESC"
                ).fetchall()
            return [self._row_to_dataset(conn, r) for r in rows]

    def get_dataset(self, dataset_id: int) -> Optional[Dataset]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_dataset(conn, row)

    def enqueue_job(self, dataset_id: int) -> Job:
        created = to_iso(utc_now())
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs (dataset_id, status, created_at)
                VALUES (?, 'queued', ?)
                """,
                (dataset_id, created),
            )
            job_id = int(cur.lastrowid)
        return self.get_job(job_id)  # type: ignore[return-value]

    def list_jobs(self) -> list[Job]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
            return [self._row_to_job(conn, r) for r in rows]

    def get_job(self, job_id: int) -> Optional[Job]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_job(conn, row)

    def _row_to_job(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Job:
        result = None
        res_row = conn.execute(
            "SELECT summary_json FROM job_results WHERE job_id = ?",
            (int(row["id"]),),
        ).fetchone()
        if res_row is not None:
            result = json.loads(res_row["summary_json"])
        return Job(
            id=int(row["id"]),
            dataset_id=int(row["dataset_id"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error_message=row["error_message"],
            result=result,
        )

    def claim_next_job(self) -> Optional[tuple[int, int, str]]:
        # atomic queued -> running; returns (job_id, dataset_id, file_path)
        started = to_iso(utc_now())
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT j.id AS job_id, j.dataset_id, d.file_path
                FROM jobs j
                JOIN datasets d ON d.id = j.dataset_id
                WHERE j.status = 'queued'
                ORDER BY j.id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            job_id = int(row["job_id"])
            cur = conn.execute(
                """
                UPDATE jobs
                SET status = 'running', started_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (started, job_id),
            )
            # another worker won the race - leave it
            if cur.rowcount == 0:
                return None
            return job_id, int(row["dataset_id"]), str(row["file_path"])

    def mark_job_succeeded(self, job_id: int, summary: dict[str, Any]) -> None:
        finished = to_iso(utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', finished_at = ?, error_message = NULL
                WHERE id = ?
                """,
                (finished, job_id),
            )
            conn.execute(
                """
                INSERT INTO job_results (job_id, summary_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    summary_json = excluded.summary_json,
                    created_at = excluded.created_at
                """,
                (job_id, json.dumps(summary), finished),
            )

    def mark_job_failed(self, job_id: int, error_message: str) -> None:
        finished = to_iso(utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', finished_at = ?, error_message = ?
                WHERE id = ?
                """,
                (finished, error_message, job_id),
            )

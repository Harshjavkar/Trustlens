"""Persistent, shared creator ingestion jobs for the local server.

Creator identity is independent of a visitor or session. Transactions serialize
requests and worker claims, so an already queued, processing or ready creator
is reused. Failed jobs require an explicit retry; startup recovery is explicit.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PLATFORM = "instagram"
ACTIVE = {"queued", "processing"}
TERMINAL = {"ready", "needs_source_links", "failed"}
COVERAGE_NOTE = "Imported posts only; full profile coverage is unverified."
_RESERVED_PATHS = {"p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct"}


def canonical_handle(value: str) -> str:
    """Accept one handle or an exact Instagram profile URL, without network IO."""
    if not isinstance(value, str):
        raise ValueError("Enter an Instagram creator handle or profile URL.")
    value = value.strip()
    if "://" in value:
        try:
            parsed = urlparse(value)
            if (parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"}
                    or parsed.netloc.lower() not in {"instagram.com", "www.instagram.com"}
                    or parsed.query or parsed.fragment or parsed.params):
                raise ValueError("Use an exact HTTPS Instagram profile URL.")
            match = re.fullmatch(r"/([A-Za-z0-9_.]{1,30})/?", parsed.path)
            if not match:
                raise ValueError("Use a profile URL, not a post, reel or other Instagram route.")
            value = match.group(1)
        except ValueError as exc:
            raise ValueError("Use an exact HTTPS Instagram profile URL.") from exc
    elif value.startswith("@"):
        value = value[1:]
    value = value.lower()
    if (not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_.]{0,28}[a-z0-9_])?", value)
            or ".." in value or value in _RESERVED_PATHS):
        raise ValueError("Use one valid Instagram creator handle, not a post or reel link.")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _coverage(value: dict | None, handle: str) -> dict:
    if value is not None and not isinstance(value, dict):
        raise ValueError("Coverage must be an object.")
    result = dict(value or {})
    if "handle" in result and canonical_handle(result["handle"]) != handle:
        raise ValueError("Coverage must describe the same creator as the job.")
    result.update({"handle": handle, "profile_complete": False,
                   "coverage_note": COVERAGE_NOTE})
    try:
        # A round trip validates JSON and detaches caller-owned mutable values.
        return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Coverage must contain finite, JSON-compatible values.") from exc


def _detail(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Job detail must be text.")
    # Worker diagnostics may contain URL tokens or authentication headers.
    text = re.sub(
        r"(?im)^.*\b(?:sessionid|csrftoken|access_token|authorization|cookie|set-cookie)\b.*$",
        "[sensitive error details redacted]", value,
    )
    return re.sub(r"https?://\S+", "[URL]", text).strip()[:2000]


class CreatorJobs:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "creator_jobs.sqlite3"
        with closing(self._connect()) as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.executescript("""
                CREATE TABLE IF NOT EXISTS creators (
                    platform TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    PRIMARY KEY(platform,handle)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','processing','ready','needs_source_links','failed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    coverage_json TEXT NOT NULL,
                    FOREIGN KEY(platform,handle) REFERENCES creators(platform,handle)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_creator_job
                    ON jobs(platform,handle) WHERE status IN ('queued','processing');
                CREATE INDEX IF NOT EXISTS queued_jobs ON jobs(status,created_at,job_id);
            """)

    def _connect(self):
        database = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        return database

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                yield database
                database.commit()
            except BaseException:
                database.rollback()
                raise

    @staticmethod
    def _row(database, handle):
        return database.execute("""SELECT j.* FROM creators c JOIN jobs j ON j.job_id=c.job_id
            WHERE c.platform=? AND c.handle=?""", (PLATFORM, handle)).fetchone()

    @staticmethod
    def _public(row) -> dict:
        return {
            "platform": row["platform"], "handle": row["handle"],
            "status": row["status"], "job_id": row["job_id"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "detail": row["detail"], "coverage": json.loads(row["coverage_json"]),
        }

    def _new_job(self, database, handle, status="queued", coverage=None, detail=None):
        job_id, timestamp = str(uuid.uuid4()), _now()
        coverage = _coverage(coverage, handle)
        detail = detail or ("Waiting for the ingestion worker." if status == "queued"
                            else "Stored creator material is available; coverage is partial.")
        database.execute("""INSERT INTO creators(platform,handle,job_id) VALUES(?,?,?)
            ON CONFLICT(platform,handle) DO UPDATE SET job_id=excluded.job_id""",
            (PLATFORM, handle, job_id))
        database.execute("""INSERT INTO jobs(job_id,platform,handle,status,created_at,
            updated_at,detail,coverage_json) VALUES(?,?,?,?,?,?,?,?)""",
            (job_id, PLATFORM, handle, status, timestamp, timestamp, detail,
             json.dumps(coverage, ensure_ascii=False, allow_nan=False)))
        return self._public(self._row(database, handle))

    def request(self, handle: str) -> dict:
        """Queue a previously unknown creator; reuse every existing job state."""
        handle = canonical_handle(handle)
        with self._transaction() as database:
            row = self._row(database, handle)
            result = self._public(row) if row else self._new_job(database, handle)
            return {**result, "reused": row is not None}

    def get(self, handle: str) -> dict | None:
        handle = canonical_handle(handle)
        with closing(self._connect()) as database:
            row = self._row(database, handle)
            return self._public(row) if row else None

    def list(self) -> list[dict]:
        with closing(self._connect()) as database:
            rows = database.execute("""SELECT j.* FROM creators c JOIN jobs j ON j.job_id=c.job_id
                WHERE c.platform=? ORDER BY c.handle""", (PLATFORM,)).fetchall()
            return [self._public(row) for row in rows]

    def claim_next(self) -> dict | None:
        """Atomically claim one queued job; safe across threads or processes."""
        with self._transaction() as database:
            row = database.execute("""SELECT j.* FROM jobs j JOIN creators c ON c.job_id=j.job_id
                WHERE j.platform=? AND j.status='queued'
                ORDER BY j.created_at,j.job_id LIMIT 1""", (PLATFORM,)).fetchone()
            if row is None:
                return None
            database.execute("UPDATE jobs SET status='processing',updated_at=?,detail=? WHERE job_id=?",
                             (_now(), "Preparing this creator's stored material.", row["job_id"]))
            return self._public(self._row(database, row["handle"]))

    def update_progress(self, handle: str, detail: str) -> dict:
        """Persist a worker stage without changing lifecycle or terminal states."""
        handle, detail = canonical_handle(handle), _detail(detail)
        with self._transaction() as database:
            row = self._row(database, handle)
            if row is None:
                raise ValueError("Request the creator before updating progress.")
            if row["status"] in ACTIVE:
                database.execute("UPDATE jobs SET updated_at=?,detail=? WHERE job_id=?",
                                 (_now(), detail, row["job_id"]))
            return self._public(self._row(database, handle))

    def finish(self, handle: str, status: str, detail: str = "", coverage: dict | None = None) -> dict:
        if status not in TERMINAL:
            raise ValueError("Finish status must be ready, needs_source_links or failed.")
        handle, detail = canonical_handle(handle), _detail(detail)
        if coverage is not None:
            coverage = _coverage(coverage, handle)
        with self._transaction() as database:
            row = self._row(database, handle)
            if row is None:
                raise ValueError("Request the creator before finishing a job.")
            if row["status"] not in ACTIVE:
                if row["status"] == status:
                    return self._public(row)
                raise ValueError("The creator has no active job; retry explicitly before processing again.")
            coverage = coverage if coverage is not None else json.loads(row["coverage_json"])
            database.execute("""UPDATE jobs SET status=?,updated_at=?,detail=?,coverage_json=?
                WHERE job_id=?""", (status, _now(), detail,
                json.dumps(coverage, ensure_ascii=False, allow_nan=False), row["job_id"]))
            return self._public(self._row(database, handle))

    def retry(self, handle: str) -> dict:
        """Create one new attempt only for failed/needs_source_links creators."""
        handle = canonical_handle(handle)
        with self._transaction() as database:
            row = self._row(database, handle)
            if row is None:
                return {**self._new_job(database, handle), "reused": False}
            if row["status"] not in {"failed", "needs_source_links"}:
                return {**self._public(row), "reused": True}
            return {**self._new_job(database, handle, coverage=json.loads(row["coverage_json"])),
                    "reused": False}

    def sync_ready(self, creators: list[dict]) -> None:
        """Sync already indexed creators without scheduling another ingestion."""
        if not isinstance(creators, list):
            raise ValueError("Indexed creators must be a list.")
        prepared = []
        for creator in creators:
            if not isinstance(creator, dict):
                raise ValueError("Each indexed creator must be an object.")
            handle = canonical_handle(creator.get("handle"))
            coverage = _coverage(creator, handle)
            # A record/media file alone is not searchable creator knowledge.
            if any(isinstance(coverage.get(key), (int, float))
                   and not isinstance(coverage.get(key), bool) and coverage[key] > 0
                   for key in ("captions", "speech", "ocr")):
                prepared.append((handle, coverage))
        with self._transaction() as database:
            for handle, coverage in prepared:
                row = self._row(database, handle)
                if row is None:
                    self._new_job(database, handle, "ready", coverage)
                    continue
                encoded = json.dumps(coverage, ensure_ascii=False, allow_nan=False)
                if row["status"] in ACTIVE:
                    # Intermediate imports can make some evidence searchable
                    # while the worker still has work to complete. Only the
                    # worker's finish() transition ends an active job.
                    if row["coverage_json"] != encoded:
                        database.execute("UPDATE jobs SET updated_at=?,coverage_json=? WHERE job_id=?",
                                         (_now(), encoded, row["job_id"]))
                    continue
                if row["status"] != "ready" or row["coverage_json"] != encoded:
                    database.execute("""UPDATE jobs SET status='ready',updated_at=?,detail=?,coverage_json=?
                        WHERE job_id=?""", (_now(), "Stored creator material is available; coverage is partial.",
                                             encoded, row["job_id"]))

    def recover_interrupted(self) -> int:
        """Requeue unfinished work once at server startup, before workers start."""
        with self._transaction() as database:
            cursor = database.execute("""UPDATE jobs SET status='queued',updated_at=?,detail=?
                WHERE platform=? AND status='processing' AND job_id IN
                    (SELECT job_id FROM creators WHERE platform=?)""",
                (_now(), "The previous worker stopped; queued again at server startup.", PLATFORM, PLATFORM))
            return cursor.rowcount

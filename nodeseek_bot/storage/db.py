from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from nodeseek_bot.storage.schema import SCHEMA_SQL
from nodeseek_bot.storage.types import (
    ContentResult,
    FeedItem,
    FetchAttempt,
    PostRow,
    ScoreResult,
    SummaryResult,
)

from nodeseek_bot.utils import canonicalize_url, now_utc, sha256_hex


_LABEL_USEFUL = "useful"
_LABEL_USELESS = "useless"


logger = logging.getLogger(__name__)


STATUS_NEW = "NEW"
STATUS_FETCHED = "FETCHED"
STATUS_SUMMARIZED = "SUMMARIZED"
STATUS_SCORED = "SCORED"
STATUS_NOTIFIED = "NOTIFIED"
STATUS_IGNORED = "IGNORED"
STATUS_FAILED = "FAILED"

CONF_RSS_ONLY = "RSS_ONLY"
CONF_FULLTEXT_HTTP = "FULLTEXT_HTTP"
CONF_FULLTEXT_BROWSER = "FULLTEXT_BROWSER"

_TERMINAL_STATUSES = {
    STATUS_FETCHED,
    STATUS_SUMMARIZED,
    STATUS_SCORED,
    STATUS_NOTIFIED,
    STATUS_IGNORED,
}


class Storage:
    def __init__(self, sqlite_path: Path):
        self._path = sqlite_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path.as_posix())
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("storage not connected")
        return self._db

    async def upsert_from_feed(self, item: FeedItem) -> int:
        async with self._lock:
            return await self._upsert_from_feed_locked(item)

    async def _upsert_from_feed_locked(self, item: FeedItem) -> int:
        url = canonicalize_url(item.url)
        url_hash = sha256_hex(url)
        now = now_utc().isoformat()

        conn = self._conn()

        await conn.execute(
            "INSERT INTO fingerprints(url_hash, last_seen_at) VALUES(?, ?) "
            "ON CONFLICT(url_hash) DO UPDATE SET last_seen_at=excluded.last_seen_at",
            (url_hash, now),
        )

        if item.guid:
            cursor = await conn.execute(
                "SELECT id FROM posts WHERE guid=?",
                (item.guid,),
            )
            row = await cursor.fetchone()
            if row is not None:
                await conn.execute(
                    "UPDATE posts SET url=?, url_hash=?, title=?, published_at=?, rss_summary=?, updated_at=? WHERE id=?",
                    (
                        url,
                        url_hash,
                        item.title,
                        item.published_at.isoformat() if item.published_at else None,
                        item.summary,
                        now,
                        row["id"],
                    ),
                )
                await conn.commit()
                return int(row["id"])

        cursor = await conn.execute(
            "SELECT id FROM posts WHERE url_hash=?",
            (url_hash,),
        )
        row = await cursor.fetchone()
        if row is not None:
            await conn.execute(
                "UPDATE posts SET url=?, title=?, published_at=?, rss_summary=?, updated_at=? WHERE id=?",
                (
                    url,
                    item.title,
                    item.published_at.isoformat() if item.published_at else None,
                    item.summary,
                    now,
                    row["id"],
                ),
            )
            await conn.commit()
            return int(row["id"])

        await conn.execute(
            "INSERT INTO posts(guid, url, url_hash, title, published_at, rss_summary, status, source_confidence, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.guid,
                url,
                url_hash,
                item.title,
                item.published_at.isoformat() if item.published_at else None,
                item.summary,
                STATUS_NEW,
                CONF_RSS_ONLY,
                now,
                now,
            ),
        )
        cursor = await conn.execute("SELECT last_insert_rowid() AS id")
        row = await cursor.fetchone()
        await conn.commit()
        return int(row["id"])

    async def get_post(self, post_id: int) -> PostRow | None:
        async with self._lock:
            conn = self._conn()
            cursor = await conn.execute(
                "SELECT * FROM posts WHERE id=?",
                (post_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return PostRow(**dict(row))

    async def list_recent_posts(self, limit: int = 10) -> list[PostRow]:
        async with self._lock:
            conn = self._conn()
            cursor = await conn.execute(
                "SELECT * FROM posts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [PostRow(**dict(r)) for r in rows]

    async def take_next_for_processing(self) -> int | None:
        async with self._lock:
            conn = self._conn()
            cursor = await conn.execute(
                "SELECT id FROM posts WHERE status IN (?, ?) ORDER BY updated_at ASC LIMIT 1",
                (STATUS_NEW, STATUS_FAILED),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return int(row["id"])

    async def set_status(self, post_id: int, status: str) -> None:
        async with self._lock:
            conn = self._conn()
            now = now_utc().isoformat()
            await conn.execute(
                "UPDATE posts SET status=?, updated_at=? WHERE id=?",
                (status, now, post_id),
            )
            await conn.commit()

    async def save_content(self, post_id: int, result: ContentResult) -> None:
        async with self._lock:
            conn = self._conn()
            now = now_utc().isoformat()
            await conn.execute(
                "INSERT INTO contents(post_id, content_text, content_hash, content_len, fetched_at) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(post_id) DO UPDATE SET content_text=excluded.content_text, content_hash=excluded.content_hash, content_len=excluded.content_len, fetched_at=excluded.fetched_at",
                (
                    post_id,
                    result.content_text,
                    result.content_hash,
                    result.content_len,
                    result.fetched_at.isoformat() if result.fetched_at else None,
                ),
            )
            await conn.execute(
                "UPDATE posts SET status=?, source_confidence=?, updated_at=? WHERE id=?",
                (STATUS_FETCHED, result.source_confidence, now, post_id),
            )
            await conn.commit()

    async def load_content(self, post_id: int) -> ContentResult | None:
        async with self._lock:
            conn = self._conn()
            cursor = await conn.execute(
                "SELECT c.content_text, c.content_hash, c.content_len, c.fetched_at, p.source_confidence "
                "FROM contents c JOIN posts p ON p.id=c.post_id WHERE c.post_id=?",
                (post_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            fetched_at = datetime.fromisoformat(row["fetched_at"]) if row["fetched_at"] else None
            return ContentResult(
                content_text=row["content_text"],
                content_hash=row["content_hash"],
                content_len=int(row["content_len"]),
                fetched_at=fetched_at,
                source_confidence=row["source_confidence"],
            )

    async def add_fetch_attempt(self, post_id: int, attempt_no: int, attempt: FetchAttempt) -> None:
        async with self._lock:
            conn = self._conn()
            now = now_utc().isoformat()
            await conn.execute(
                "INSERT INTO fetch_attempts(post_id, attempt_no, method, ok, http_status, error_type, error_detail, duration_ms, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    post_id,
                    attempt_no,
                    attempt.method,
                    1 if attempt.ok else 0,
                    attempt.http_status,
                    attempt.error_type,
                    attempt.error_detail,
                    attempt.duration_ms,
                    now,
                ),
            )
            await conn.commit()

    async def save_summary(self, post_id: int, summary: SummaryResult) -> None:
        async with self._lock:
            conn = self._conn()
            now = now_utc().isoformat()
            await conn.execute(
                "INSERT INTO ai_summaries(post_id, model, prompt_version, summary_text, key_points_json, actions_json, token_in, token_out, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(post_id) DO UPDATE SET model=excluded.model, prompt_version=excluded.prompt_version, summary_text=excluded.summary_text, key_points_json=excluded.key_points_json, actions_json=excluded.actions_json, token_in=excluded.token_in, token_out=excluded.token_out, created_at=excluded.created_at",
                (
                    post_id,
                    summary.model,
                    summary.prompt_version,
                    summary.summary_text,
                    json.dumps(summary.key_points, ensure_ascii=False),
                    json.dumps(summary.actions, ensure_ascii=False),
                    summary.token_in,
                    summary.token_out,
                    now,
                ),
            )
            await conn.execute(
                "UPDATE posts SET status=?, updated_at=? WHERE id=?",
                (STATUS_SUMMARIZED, now, post_id),
            )
            await conn.commit()

    async def load_summary(self, post_id: int) -> SummaryResult | None:
        async with self._lock:
            conn = self._conn()
            cursor = await conn.execute(
                "SELECT * FROM ai_summaries WHERE post_id=?",
                (post_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            key_points = json.loads(row["key_points_json"]) if row["key_points_json"] else []
            actions = json.loads(row["actions_json"]) if row["actions_json"] else []
            return SummaryResult(
                model=row["model"],
                prompt_version=row["prompt_version"],
                summary_text=row["summary_text"],
                key_points=key_points,
                actions=actions,
                token_in=row["token_in"],
                token_out=row["token_out"],
            )

    async def save_score(self, post_id: int, score: ScoreResult) -> None:
        async with self._lock:
            conn = self._conn()
            now = now_utc().isoformat()
            await conn.execute(
                "INSERT INTO scores(post_id, score_total, decision, explain_json, created_at) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(post_id) DO UPDATE SET score_total=excluded.score_total, decision=excluded.decision, explain_json=excluded.explain_json, created_at=excluded.created_at",
                (
                    post_id,
                    float(score.score_total),
                    score.decision,
                    json.dumps(score.explain, ensure_ascii=False),
                    now,
                ),
            )
            await conn.execute(
                "UPDATE posts SET status=?, updated_at=? WHERE id=?",
                (STATUS_SCORED, now, post_id),
            )
            await conn.commit()

    async def load_score(self, post_id: int) -> ScoreResult | None:
        async with self._lock:
            conn = self._conn()
            cursor = await conn.execute(
                "SELECT * FROM scores WHERE post_id=?",
                (post_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return ScoreResult(
                score_total=float(row["score_total"]),
                decision=row["decision"],
                explain=json.loads(row["explain_json"]),
            )

    async def record_delivery(self, post_id: int, target_chat_id: int, message_id: int) -> None:
        async with self._lock:
            conn = self._conn()
            now = now_utc().isoformat()
            await conn.execute(
                "INSERT OR IGNORE INTO deliveries(post_id, target_chat_id, message_id, delivered_at) VALUES(?, ?, ?, ?)",
                (post_id, target_chat_id, message_id, now),
            )
            await conn.execute(
                "UPDATE posts SET status=?, updated_at=? WHERE id=?",
                (STATUS_NOTIFIED, now, post_id),
            )
            await conn.commit()

    async def has_delivery(self, post_id: int, target_chat_id: int) -> bool:
        async with self._lock:
            conn = self._conn()
            cursor = await conn.execute(
                "SELECT 1 FROM deliveries WHERE post_id=? AND target_chat_id=?",
                (post_id, target_chat_id),
            )
            return (await cursor.fetchone()) is not None

    async def update_fingerprint_processed(self, url_hash: str, decision: str) -> None:
        async with self._lock:
            conn = self._conn()
            now = now_utc().isoformat()
            await conn.execute(
                "UPDATE fingerprints SET last_processed_at=?, last_decision=? WHERE url_hash=?",
                (now, decision, url_hash),
            )
            await conn.commit()

    async def upsert_label(self, post_id: int, label: str, labeled_by: int | None = None) -> None:
        label = (label or "").strip().lower()
        if label not in {_LABEL_USEFUL, _LABEL_USELESS}:
            raise ValueError(f"invalid label: {label}")

        async with self._lock:
            conn = self._conn()
            now = now_utc().isoformat()
            await conn.execute(
                "INSERT INTO labels(post_id, label, labeled_by, labeled_at) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(post_id) DO UPDATE SET label=excluded.label, labeled_by=excluded.labeled_by, labeled_at=excluded.labeled_at",
                (post_id, label, labeled_by, now),
            )
            await conn.commit()

    async def count_labels(self) -> int:
        async with self._lock:
            conn = self._conn()
            cursor = await conn.execute("SELECT COUNT(1) AS n FROM labels")
            row = await cursor.fetchone()
            return int(row["n"] if row is not None else 0)

    async def get_labeled_scores(self, limit: int | None = None) -> list[tuple[float, int]]:
        """Return list of (score_total, y) where y=1 for useful else 0."""
        async with self._lock:
            conn = self._conn()
            sql = (
                "SELECT s.score_total AS score_total, l.label AS label "
                "FROM labels l JOIN scores s ON s.post_id=l.post_id "
                "ORDER BY l.labeled_at ASC"
            )
            args: tuple = ()
            if limit is not None:
                sql += " LIMIT ?"
                args = (int(limit),)
            cursor = await conn.execute(sql, args)
            rows = await cursor.fetchall()

        out: list[tuple[float, int]] = []
        for r in rows or []:
            sc = float(r["score_total"])
            lab = str(r["label"] or "").strip().lower()
            y = 1 if lab == _LABEL_USEFUL else 0
            out.append((sc, y))
        return out

    async def reset_post(self, post_id: int) -> None:
        """Reset a post for reprocessing.

        - Clears deliveries so it can be pushed again.
        - Clears fetch attempts, content, AI summary, score.
        - Sets status back to NEW and confidence back to RSS_ONLY.
        """
        async with self._lock:
            conn = self._conn()
            now = now_utc().isoformat()

            await conn.execute("DELETE FROM deliveries WHERE post_id=?", (post_id,))
            await conn.execute("DELETE FROM fetch_attempts WHERE post_id=?", (post_id,))
            await conn.execute("DELETE FROM contents WHERE post_id=?", (post_id,))
            await conn.execute("DELETE FROM ai_summaries WHERE post_id=?", (post_id,))
            await conn.execute("DELETE FROM scores WHERE post_id=?", (post_id,))

            await conn.execute(
                "UPDATE posts SET status=?, source_confidence=?, updated_at=? WHERE id=?",
                (STATUS_NEW, CONF_RSS_ONLY, now, post_id),
            )
            await conn.commit()

    async def cleanup(self, data_retention_days: int, fingerprint_retention_days: int) -> None:
        async with self._lock:
            conn = self._conn()
            now = now_utc()
            content_cutoff = (now - timedelta(days=data_retention_days)).isoformat()
            fp_cutoff = (now - timedelta(days=fingerprint_retention_days)).isoformat()

            # Drop old content bodies and old fetch attempts
            await conn.execute("UPDATE contents SET content_text=NULL WHERE fetched_at < ?", (content_cutoff,))
            await conn.execute("DELETE FROM fetch_attempts WHERE created_at < ?", (content_cutoff,))

            # Summaries/scores are small, keep them for retention window only
            await conn.execute("DELETE FROM ai_summaries WHERE created_at < ?", (content_cutoff,))
            await conn.execute("DELETE FROM scores WHERE created_at < ?", (content_cutoff,))

            # Deliveries are small; keep for retention window
            await conn.execute("DELETE FROM deliveries WHERE delivered_at < ?", (content_cutoff,))

            # Remove old post rows (keep fingerprints for long-term dedup)
            terminal = tuple(sorted(_TERMINAL_STATUSES))
            placeholders = ",".join(["?"] * len(terminal))
            await conn.execute(
                f"DELETE FROM posts WHERE updated_at < ? AND status IN ({placeholders})",
                (content_cutoff, *terminal),
            )

            # Fingerprints can be long-lived
            await conn.execute("DELETE FROM fingerprints WHERE last_seen_at < ?", (fp_cutoff,))

            await conn.commit()

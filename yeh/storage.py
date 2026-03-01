import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.parser import Parser
from pathlib import Path

from yeh import routes


@dataclass(frozen=True)
class SessionRecord:
    cookie_jar_json: str
    csrf_token: str | None
    final_url: str
    authenticated_at: str


@dataclass(frozen=True)
class TopicSyncState:
    summary_hash: str | None
    last_synced_at: str | None


@dataclass(frozen=True)
class MailboxSummary:
    mailbox: routes.Mailbox
    mailbox_url: str
    topic_count: int


@dataclass(frozen=True)
class StoredTopicSummary:
    topic_id: str
    sender: str
    subject: str
    snippet: str
    when_text: str
    topic_url: str
    message_count: int
    has_attachments: bool
    mailboxes: list[routes.Mailbox]


@dataclass(frozen=True)
class StoredMessage:
    message_id: str
    source_url: str
    content_text: str
    updated_at: str
    has_attachment: bool


@dataclass(frozen=True)
class StoredTopicPage:
    topics: list[StoredTopicSummary]
    total_count: int


@dataclass(frozen=True)
class _TopicPreview:
    sender: str
    subject: str
    snippet: str
    when_text: str


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), timeout=60.0)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self._sessions_email_col = (
            self._pick_col("sessions", ["hey_email", "email"]) or "hey_email"
        )

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        # WAL must be set before any writes; executescript issues an implicit
        # COMMIT so it cannot run inside an open transaction.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("DROP TABLE IF EXISTS auth_audit")
        self.conn.commit()
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
              hey_email TEXT PRIMARY KEY,
              cookie_jar_json TEXT NOT NULL,
              csrf_token TEXT,
              final_url TEXT NOT NULL,
              authenticated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS topics (
              hey_email TEXT NOT NULL,
              topic_id TEXT NOT NULL,
              topic_url TEXT NOT NULL,
              sender TEXT NOT NULL,
              subject TEXT NOT NULL,
              snippet TEXT NOT NULL,
              when_text TEXT NOT NULL,
              summary_hash TEXT,
              last_synced_at TEXT,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              PRIMARY KEY (hey_email, topic_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
              hey_email TEXT NOT NULL,
              topic_id TEXT NOT NULL,
              message_id TEXT NOT NULL,
              source_url TEXT NOT NULL,
              content_text TEXT NOT NULL,
              content_sha256 TEXT NOT NULL,
              first_fetched_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (hey_email, message_id)
            );

            CREATE TABLE IF NOT EXISTS topic_mailboxes (
              hey_email TEXT NOT NULL,
              topic_id TEXT NOT NULL,
              mailbox_key TEXT NOT NULL,
              mailbox_url TEXT NOT NULL,
              assigned_at TEXT NOT NULL,
              PRIMARY KEY (hey_email, topic_id, mailbox_key)
            );

            CREATE TABLE IF NOT EXISTS imap_flags (
              hey_email TEXT NOT NULL,
              topic_id TEXT NOT NULL,
              seen INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (hey_email, topic_id)
            );

            CREATE INDEX IF NOT EXISTS idx_topics_email_last_seen
              ON topics (hey_email, last_seen_at DESC);

            CREATE INDEX IF NOT EXISTS idx_topic_mailboxes_email_mailbox_topic
              ON topic_mailboxes (hey_email, mailbox_key, topic_id);

            CREATE INDEX IF NOT EXISTS idx_topic_mailboxes_email_topic
              ON topic_mailboxes (hey_email, topic_id);

            CREATE INDEX IF NOT EXISTS idx_messages_email_topic
              ON messages (hey_email, topic_id);

            CREATE INDEX IF NOT EXISTS idx_imap_flags_email_seen
              ON imap_flags (hey_email, seen);
            """
        )
        self._ensure_column("topics", "summary_hash", "TEXT")
        self._ensure_column("topics", "last_synced_at", "TEXT")

    def save_session(
        self,
        hey_email: str,
        cookie_jar_json: str,
        csrf_token: str | None,
        final_url: str,
    ) -> None:
        now_iso = datetime.now(tz=UTC).isoformat()

        session_sql = f"""
            INSERT INTO sessions ({self._sessions_email_col}, cookie_jar_json, csrf_token, final_url, authenticated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT({self._sessions_email_col}) DO UPDATE SET
              cookie_jar_json = excluded.cookie_jar_json,
              csrf_token = excluded.csrf_token,
              final_url = excluded.final_url,
              authenticated_at = excluded.authenticated_at
        """
        self.conn.execute(
            session_sql,
            (hey_email, cookie_jar_json, csrf_token, final_url, now_iso),
        )
        self.conn.commit()

    def load_session(self, hey_email: str) -> SessionRecord | None:
        sql = f"SELECT cookie_jar_json, csrf_token, final_url, authenticated_at FROM sessions WHERE {self._sessions_email_col} = ?"
        row = self.conn.execute(sql, (hey_email,)).fetchone()
        if row is None:
            return None
        return SessionRecord(
            cookie_jar_json=row["cookie_jar_json"],
            csrf_token=row["csrf_token"],
            final_url=row["final_url"],
            authenticated_at=str(row["authenticated_at"]),
        )

    def upsert_topic(
        self,
        hey_email: str,
        topic_id: str,
        topic_url: str,
        sender: str,
        subject: str,
        snippet: str,
        when_text: str,
        summary_hash: str,
    ) -> None:
        now_iso = datetime.now(tz=UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO topics (
              hey_email,
              topic_id,
              topic_url,
              sender,
              subject,
              snippet,
              when_text,
              summary_hash,
              first_seen_at,
              last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hey_email, topic_id) DO UPDATE SET
              topic_url = excluded.topic_url,
              sender = excluded.sender,
              subject = excluded.subject,
              snippet = excluded.snippet,
              when_text = excluded.when_text,
              summary_hash = excluded.summary_hash,
              last_seen_at = excluded.last_seen_at
            """,
            (
                hey_email,
                topic_id,
                topic_url,
                sender,
                subject,
                snippet,
                when_text,
                summary_hash,
                now_iso,
                now_iso,
            ),
        )
        self.conn.commit()

    def touch_topic(self, hey_email: str, topic_id: str, topic_url: str) -> None:
        now_iso = datetime.now(tz=UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO topics (
              hey_email,
              topic_id,
              topic_url,
              sender,
              subject,
              snippet,
              when_text,
              summary_hash,
              first_seen_at,
              last_seen_at
            )
            VALUES (?, ?, ?, '', '', '', '', '', ?, ?)
            ON CONFLICT(hey_email, topic_id) DO UPDATE SET
              topic_url = excluded.topic_url,
              last_seen_at = excluded.last_seen_at
            """,
            (hey_email, topic_id, topic_url, now_iso, now_iso),
        )
        self.conn.commit()

    def topic_sync_state(self, hey_email: str, topic_id: str) -> TopicSyncState | None:
        row = self.conn.execute(
            """
            SELECT summary_hash, last_synced_at FROM topics
            WHERE hey_email = ? AND topic_id = ?
            """,
            (hey_email, topic_id),
        ).fetchone()
        if row is None:
            return None
        summary_hash = row["summary_hash"]
        last_synced_at = row["last_synced_at"]
        return TopicSyncState(
            summary_hash=str(summary_hash) if isinstance(summary_hash, str) else None,
            last_synced_at=(
                str(last_synced_at) if isinstance(last_synced_at, str) else None
            ),
        )

    def mark_topic_synced(self, hey_email: str, topic_id: str) -> None:
        now_iso = datetime.now(tz=UTC).isoformat()
        self.conn.execute(
            """
            UPDATE topics
            SET last_synced_at = ?
            WHERE hey_email = ? AND topic_id = ?
            """,
            (now_iso, hey_email, topic_id),
        )
        self.conn.commit()

    def assign_topic_mailbox(
        self,
        hey_email: str,
        topic_id: str,
        mailbox: routes.Mailbox,
        mailbox_url: str,
    ) -> None:
        now_iso = datetime.now(tz=UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO topic_mailboxes (
              hey_email,
              topic_id,
              mailbox_key,
              mailbox_url,
              assigned_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(hey_email, topic_id, mailbox_key) DO UPDATE SET
              mailbox_url = excluded.mailbox_url,
              assigned_at = excluded.assigned_at
            """,
            (hey_email, topic_id, mailbox.value, mailbox_url, now_iso),
        )
        self.conn.commit()

    def upsert_message_text(
        self,
        hey_email: str,
        topic_id: str,
        message_id: str,
        source_url: str,
        content_text: str,
    ) -> bool:
        now_iso = datetime.now(tz=UTC).isoformat()
        content_sha256 = hashlib.sha256(content_text.encode("utf-8")).hexdigest()

        existing = self.conn.execute(
            """
            SELECT content_sha256 FROM messages
            WHERE hey_email = ? AND message_id = ?
            """,
            (hey_email, message_id),
        ).fetchone()

        if existing is None:
            self.conn.execute(
                """
                INSERT INTO messages (
                  hey_email,
                  topic_id,
                  message_id,
                  source_url,
                  content_text,
                  content_sha256,
                  first_fetched_at,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hey_email,
                    topic_id,
                    message_id,
                    source_url,
                    content_text,
                    content_sha256,
                    now_iso,
                    now_iso,
                ),
            )
            self.conn.commit()
            return False

        changed = str(existing["content_sha256"]) != content_sha256
        if changed:
            self.conn.execute(
                """
                UPDATE messages
                SET
                  topic_id = ?,
                  source_url = ?,
                  content_text = ?,
                  content_sha256 = ?,
                  updated_at = ?
                WHERE hey_email = ? AND message_id = ?
                """,
                (
                    topic_id,
                    source_url,
                    content_text,
                    content_sha256,
                    now_iso,
                    hey_email,
                    message_id,
                ),
            )
            self.conn.commit()
        return changed

    def list_mailboxes(self, hey_email: str) -> list[MailboxSummary]:
        # group only by mailbox_key; pick an arbitrary URL via MAX to avoid
        # duplicate rows when the same mailbox was synced via different URLs.
        rows = self.conn.execute(
            """
            SELECT mailbox_key, MAX(mailbox_url) AS mailbox_url, COUNT(*) AS topic_count
            FROM topic_mailboxes
            WHERE hey_email = ?
            GROUP BY mailbox_key
            ORDER BY mailbox_key
            """,
            (hey_email,),
        ).fetchall()
        out: list[MailboxSummary] = []
        for row in rows:
            raw_key = row["mailbox_key"]
            if not isinstance(raw_key, str):
                continue
            try:
                mailbox = routes.parse_mailbox(raw_key)
            except ValueError:
                continue
            out.append(
                MailboxSummary(
                    mailbox=mailbox,
                    mailbox_url=str(row["mailbox_url"]),
                    topic_count=int(row["topic_count"]),
                )
            )
        return out

    def list_topics_page(
        self,
        hey_email: str,
        mailbox: routes.Mailbox | None,
        limit: int,
        offset: int,
    ) -> StoredTopicPage:
        total_count = self._count_topics(hey_email=hey_email, mailbox=mailbox)
        params: tuple[object, ...]
        sql = """
            SELECT
              t.topic_id,
              t.sender,
              t.subject,
              t.snippet,
              t.when_text,
              t.topic_url
            FROM topics t
            WHERE t.hey_email = ?
        """
        if mailbox is not None:
            sql += """
            AND EXISTS (
              SELECT 1
              FROM topic_mailboxes tx
              WHERE tx.hey_email = t.hey_email
                AND tx.topic_id = t.topic_id
                AND tx.mailbox_key = ?
            )
            """
            params = (hey_email, mailbox.value)
        else:
            params = (hey_email,)

        sql += """
            ORDER BY t.last_seen_at DESC
            LIMIT ?
            OFFSET ?
        """
        params = (*params, limit, offset)

        base_rows = self.conn.execute(sql, params).fetchall()
        topic_ids = [str(row["topic_id"]) for row in base_rows]
        message_counts = self._message_counts(hey_email=hey_email, topic_ids=topic_ids)
        attachment_flags = self._topic_attachment_flags(
            hey_email=hey_email,
            topic_ids=topic_ids,
        )
        previews = self._topic_message_previews(
            hey_email=hey_email, topic_ids=topic_ids
        )
        mailbox_map = self._topic_mailboxes(hey_email=hey_email, topic_ids=topic_ids)

        topics: list[StoredTopicSummary] = []
        for row in base_rows:
            topic_id = str(row["topic_id"])
            sender = str(row["sender"])
            subject = str(row["subject"])
            snippet = str(row["snippet"])
            when_text = str(row["when_text"])
            p = previews.get(topic_id)
            if p is not None:
                if not sender:
                    sender = p.sender
                if not subject:
                    subject = p.subject
                if not snippet:
                    snippet = p.snippet
                if not when_text:
                    when_text = p.when_text
            if not sender:
                sender = "(unknown)"
            if not subject:
                subject = "(no subject)"
            topics.append(
                StoredTopicSummary(
                    topic_id=topic_id,
                    sender=sender,
                    subject=subject,
                    snippet=snippet,
                    when_text=when_text,
                    topic_url=str(row["topic_url"]),
                    message_count=message_counts.get(topic_id, 0),
                    has_attachments=attachment_flags.get(topic_id, False),
                    mailboxes=mailbox_map.get(topic_id, []),
                )
            )
        return StoredTopicPage(topics=topics, total_count=total_count)

    def topic_message_count(self, hey_email: str, topic_id: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM messages
            WHERE hey_email = ? AND topic_id = ?
            """,
            (hey_email, topic_id),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def load_latest_message_text(self, hey_email: str, topic_id: str) -> str | None:
        """Return content_text of the most recently updated message for a topic."""
        row = self.conn.execute(
            """
            SELECT content_text FROM messages
            WHERE hey_email = ? AND topic_id = ?
            ORDER BY updated_at DESC, message_id DESC
            LIMIT 1
            """,
            (hey_email, topic_id),
        ).fetchone()
        if row is None:
            return None
        raw = row["content_text"]
        return str(raw) if isinstance(raw, str) and raw else None

    def load_topic_messages(self, hey_email: str, topic_id: str) -> list[StoredMessage]:
        rows = self.conn.execute(
            """
            SELECT
              message_id,
              source_url,
              content_text,
              updated_at,
              CASE
                WHEN instr(lower(content_text), 'content-disposition: attachment') > 0 THEN 1
                WHEN instr(lower(content_text), 'filename=') > 0 THEN 1
                ELSE 0
              END AS has_attachment
            FROM messages
            WHERE hey_email = ? AND topic_id = ?
            ORDER BY first_fetched_at ASC, message_id ASC
            """,
            (hey_email, topic_id),
        ).fetchall()
        return [
            StoredMessage(
                message_id=str(row["message_id"]),
                source_url=str(row["source_url"]),
                content_text=str(row["content_text"]),
                updated_at=str(row["updated_at"]),
                has_attachment=bool(int(row["has_attachment"])),
            )
            for row in rows
        ]

    def set_topic_seen(self, hey_email: str, topic_id: str, seen: bool) -> None:
        now_iso = datetime.now(tz=UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO imap_flags (hey_email, topic_id, seen, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(hey_email, topic_id) DO UPDATE SET
              seen = excluded.seen,
              updated_at = excluded.updated_at
            """,
            (hey_email, topic_id, 1 if seen else 0, now_iso),
        )
        self.conn.commit()

    def topic_seen_map(self, hey_email: str, topic_ids: list[str]) -> dict[str, bool]:
        if not topic_ids:
            return {}
        placeholders = ", ".join("?" for _ in topic_ids)
        rows = self.conn.execute(
            f"""
            SELECT topic_id, seen
            FROM imap_flags
            WHERE hey_email = ? AND topic_id IN ({placeholders})
            """,
            (hey_email, *topic_ids),
        ).fetchall()
        return {str(row["topic_id"]): bool(int(row["seen"])) for row in rows}

    def count_unseen_topics(
        self, hey_email: str, mailbox: routes.Mailbox | None = None
    ) -> int:
        params: tuple[object, ...] = (hey_email,)
        sql = """
            SELECT COUNT(*) AS n
            FROM topics t
            LEFT JOIN imap_flags f
              ON f.hey_email = t.hey_email AND f.topic_id = t.topic_id
            WHERE t.hey_email = ?
              AND COALESCE(f.seen, 0) = 0
        """
        if mailbox is not None:
            sql += """
              AND EXISTS (
                SELECT 1
                FROM topic_mailboxes tx
                WHERE tx.hey_email = t.hey_email
                  AND tx.topic_id = t.topic_id
                  AND tx.mailbox_key = ?
              )
            """
            params = (hey_email, mailbox.value)
        row = self.conn.execute(sql, params).fetchone()
        return int(row["n"]) if row is not None else 0

    def _message_counts(self, hey_email: str, topic_ids: list[str]) -> dict[str, int]:
        if not topic_ids:
            return {}
        placeholders = ", ".join("?" for _ in topic_ids)
        rows = self.conn.execute(
            f"""
            SELECT topic_id, COUNT(*) AS n
            FROM messages
            WHERE hey_email = ? AND topic_id IN ({placeholders})
            GROUP BY topic_id
            """,
            (hey_email, *topic_ids),
        ).fetchall()
        return {str(row["topic_id"]): int(row["n"]) for row in rows}

    def _topic_mailboxes(
        self, hey_email: str, topic_ids: list[str]
    ) -> dict[str, list[routes.Mailbox]]:
        if not topic_ids:
            return {}
        placeholders = ", ".join("?" for _ in topic_ids)
        rows = self.conn.execute(
            f"""
            SELECT topic_id, mailbox_key
            FROM topic_mailboxes
            WHERE hey_email = ? AND topic_id IN ({placeholders})
            ORDER BY topic_id, mailbox_key
            """,
            (hey_email, *topic_ids),
        ).fetchall()

        out: dict[str, list[routes.Mailbox]] = {}
        for row in rows:
            topic_id = str(row["topic_id"])
            raw_key = row["mailbox_key"]
            if not isinstance(raw_key, str):
                continue
            try:
                mailbox = routes.parse_mailbox(raw_key)
            except ValueError:
                continue
            out.setdefault(topic_id, []).append(mailbox)
        return out

    def _topic_attachment_flags(
        self, hey_email: str, topic_ids: list[str]
    ) -> dict[str, bool]:
        if not topic_ids:
            return {}
        placeholders = ", ".join("?" for _ in topic_ids)
        rows = self.conn.execute(
            f"""
            SELECT
              topic_id,
              MAX(
                CASE
                  WHEN instr(lower(content_text), 'content-disposition: attachment') > 0 THEN 1
                  WHEN instr(lower(content_text), 'filename=') > 0 THEN 1
                  ELSE 0
                END
              ) AS has_attachment
            FROM messages
            WHERE hey_email = ? AND topic_id IN ({placeholders})
            GROUP BY topic_id
            """,
            (hey_email, *topic_ids),
        ).fetchall()
        return {str(row["topic_id"]): bool(int(row["has_attachment"])) for row in rows}

    def _topic_message_previews(
        self, hey_email: str, topic_ids: list[str]
    ) -> dict[str, _TopicPreview]:
        if not topic_ids:
            return {}
        placeholders = ", ".join("?" for _ in topic_ids)
        rows = self.conn.execute(
            f"""
            SELECT topic_id, content_text
            FROM (
              SELECT
                topic_id,
                content_text,
                ROW_NUMBER() OVER (
                  PARTITION BY topic_id
                  ORDER BY updated_at DESC, message_id DESC
                ) AS rn
              FROM messages
              WHERE hey_email = ? AND topic_id IN ({placeholders})
            )
            WHERE rn = 1
            """,
            (hey_email, *topic_ids),
        ).fetchall()
        out: dict[str, _TopicPreview] = {}
        for row in rows:
            topic_id = str(row["topic_id"])
            raw = row["content_text"]
            if not isinstance(raw, str) or not raw:
                continue
            out[topic_id] = self._parse_preview(raw)
        return out

    def _parse_preview(self, raw: str) -> _TopicPreview:
        try:
            msg = Parser(policy=policy.default).parsestr(raw)
        except (TypeError, ValueError, LookupError):  # fmt: skip
            return _TopicPreview(
                sender="", subject="", snippet=self._clip(raw), when_text=""
            )

        sender = str(msg.get("From", "")).strip()
        subject = str(msg.get("Subject", "")).strip()
        when_text = str(msg.get("Date", "")).strip()
        snippet = self._clip(self._message_text(msg))
        if not snippet:
            snippet = self._clip(raw)
        return _TopicPreview(
            sender=sender,
            subject=subject,
            snippet=snippet,
            when_text=when_text,
        )

    def _message_text(self, msg) -> str:
        if msg.is_multipart():
            out: list[str] = []
            for part in msg.walk():
                if part.get_content_maintype() != "text":
                    continue
                disp = str(part.get("Content-Disposition", "")).lower()
                if disp.startswith("attachment"):
                    continue
                try:
                    text = part.get_content()
                except (TypeError, ValueError, LookupError):  # fmt: skip
                    text = ""
                if isinstance(text, str) and text.strip():
                    out.append(text.strip())
            return "\n\n".join(out)
        try:
            text = msg.get_content()
        except (TypeError, ValueError, LookupError):  # fmt: skip
            text = ""
        return text if isinstance(text, str) else ""

    def _clip(self, text: str, n: int = 240) -> str:
        return " ".join(text.split())[:n]

    def _count_topics(self, hey_email: str, mailbox: routes.Mailbox | None) -> int:
        if mailbox is None:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS n FROM topics WHERE hey_email = ?
                """,
                (hey_email,),
            ).fetchone()
            return int(row["n"]) if row is not None else 0

        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM topics t
            WHERE t.hey_email = ?
              AND EXISTS (
                SELECT 1
                FROM topic_mailboxes tx
                WHERE tx.hey_email = t.hey_email
                  AND tx.topic_id = t.topic_id
                  AND tx.mailbox_key = ?
              )
            """,
            (hey_email, mailbox.value),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def _pick_col(self, table: str, candidates: list[str]) -> str | None:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        names = {row["name"] for row in rows}
        for candidate in candidates:
            if candidate in names:
                return candidate
        return None

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        names = {str(row["name"]) for row in rows}
        if column in names:
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

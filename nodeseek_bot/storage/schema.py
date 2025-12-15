from __future__ import annotations

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guid TEXT,
  url TEXT NOT NULL,
  url_hash TEXT NOT NULL,
  title TEXT NOT NULL,
  published_at TEXT,
  rss_summary TEXT,
  status TEXT NOT NULL,
  source_confidence TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_guid ON posts(guid) WHERE guid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_url_hash ON posts(url_hash);
CREATE INDEX IF NOT EXISTS ix_posts_status_updated_at ON posts(status, updated_at);

CREATE TABLE IF NOT EXISTS contents (
  post_id INTEGER PRIMARY KEY,
  content_text TEXT,
  content_hash TEXT,
  content_len INTEGER NOT NULL,
  fetched_at TEXT,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_contents_fetched_at ON contents(fetched_at);

CREATE TABLE IF NOT EXISTS fetch_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  attempt_no INTEGER NOT NULL,
  method TEXT NOT NULL,
  ok INTEGER NOT NULL,
  http_status INTEGER,
  error_type TEXT,
  error_detail TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_fetch_attempts_post_created ON fetch_attempts(post_id, created_at);

CREATE TABLE IF NOT EXISTS ai_summaries (
  post_id INTEGER PRIMARY KEY,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  summary_text TEXT NOT NULL,
  key_points_json TEXT,
  actions_json TEXT,
  token_in INTEGER,
  token_out INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scores (
  post_id INTEGER PRIMARY KEY,
  score_total REAL NOT NULL,
  decision TEXT NOT NULL,
  explain_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  target_chat_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  delivered_at TEXT NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_deliveries_post_target ON deliveries(post_id, target_chat_id);

CREATE TABLE IF NOT EXISTS labels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  labeled_by INTEGER,
  labeled_at TEXT NOT NULL,
  FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_labels_post_id ON labels(post_id);
CREATE INDEX IF NOT EXISTS ix_labels_labeled_at ON labels(labeled_at);
CREATE INDEX IF NOT EXISTS ix_labels_label ON labels(label);

CREATE TABLE IF NOT EXISTS fingerprints (
  url_hash TEXT PRIMARY KEY,
  last_seen_at TEXT NOT NULL,
  last_processed_at TEXT,
  last_decision TEXT
);

CREATE INDEX IF NOT EXISTS ix_fingerprints_last_seen ON fingerprints(last_seen_at);
"""

"""SQLite state store. Lives on the `state` branch between GitHub Actions runs."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Iterable

import numpy as np

from .models import Article, Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  id TEXT PRIMARY KEY, url TEXT, title TEXT, source TEXT, published REAL,
  description TEXT, first_seen REAL, emb BLOB, event_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_event ON articles(event_id);
CREATE TABLE IF NOT EXISTS article_feeds (article_id TEXT, feed_key TEXT, PRIMARY KEY (article_id, feed_key));
CREATE INDEX IF NOT EXISTS idx_feeds_key ON article_feeds(feed_key);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, centroid BLOB, n_articles INTEGER, outlets INTEGER,
  first_seen REAL, last_updated REAL, rep_article TEXT, tags TEXT, summary TEXT, summary_version INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS impressions (
  edition_id TEXT, reader TEXT, date TEXT, event_id TEXT, section TEXT, position INTEGER,
  score REAL, features TEXT, ranker_version TEXT, team TEXT, is_update INTEGER, is_bubble INTEGER, ts REAL
);
CREATE INDEX IF NOT EXISTS idx_imp_reader ON impressions(reader, event_id);
CREATE TABLE IF NOT EXISTS sends (
  reader TEXT, date TEXT, kind TEXT, status TEXT, ts REAL, edition_id TEXT,
  PRIMARY KEY (reader, date, kind)
);
CREATE TABLE IF NOT EXISTS editions (
  id TEXT PRIMARY KEY, reader TEXT, date TEXT, kind TEXT, ts REAL, stories INTEGER,
  claims_total INTEGER, claims_supported INTEGER, numbers_removed INTEGER,
  input_tokens INTEGER, output_tokens INTEGER, ms INTEGER, notes TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float16).tobytes()


def _from_blob(b: bytes | None) -> np.ndarray | None:
    if b is None:
        return None
    return np.frombuffer(b, dtype=np.float16).astype(np.float32)


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    @classmethod
    def open(cls, state_dir: str) -> "Store":
        return cls(os.path.join(state_dir, "brief.db"))

    def close(self):
        self.db.commit()
        self.db.close()

    def commit(self):
        self.db.commit()

    # ---- meta ---------------------------------------------------------------
    def get_meta(self, key: str, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # ---- articles -----------------------------------------------------------
    def known_article_ids(self, ids: Iterable[str]) -> set[str]:
        ids = list(ids)
        out: set[str] = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = "SELECT id FROM articles WHERE id IN (%s)" % ",".join("?" * len(chunk))
            out.update(r["id"] for r in self.db.execute(q, chunk))
        return out

    def add_feed_links(self, pairs: Iterable[tuple[str, str]]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO article_feeds(article_id, feed_key) VALUES (?, ?)", list(pairs))

    def insert_article(self, a: Article) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO articles(id,url,title,source,published,description,first_seen,emb,event_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (a.id, a.url, a.title, a.source, a.published, a.description, a.first_seen,
             _to_blob(a.emb) if a.emb is not None else None, a.event_id))
        self.add_feed_links((a.id, f) for f in a.feeds)

    def set_article_event(self, article_id: str, event_id: str) -> None:
        self.db.execute("UPDATE articles SET event_id=? WHERE id=?", (event_id, article_id))

    def articles_for_event(self, event_id: str) -> list[Article]:
        rows = self.db.execute("SELECT * FROM articles WHERE event_id=? ORDER BY published", (event_id,)).fetchall()
        return [self._row_article(r) for r in rows]

    def article(self, article_id: str) -> Article | None:
        r = self.db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        return self._row_article(r) if r else None

    def _row_article(self, r) -> Article:
        feeds = {x["feed_key"] for x in self.db.execute("SELECT feed_key FROM article_feeds WHERE article_id=?", (r["id"],))}
        return Article(id=r["id"], url=r["url"], title=r["title"], source=r["source"], published=r["published"],
                       description=r["description"] or "", feeds=feeds, emb=_from_blob(r["emb"]),
                       event_id=r["event_id"], first_seen=r["first_seen"])

    # ---- events -------------------------------------------------------------
    def upsert_event(self, e: Event) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO events(id,centroid,n_articles,outlets,first_seen,last_updated,rep_article,tags,summary,summary_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (e.id, _to_blob(e.centroid), e.n_articles, e.outlets, e.first_seen, e.last_updated, e.rep_article,
             json.dumps(e.tags or {}), json.dumps(e.summary) if e.summary is not None else None, e.summary_version))

    def delete_event(self, event_id: str) -> None:
        self.db.execute("DELETE FROM events WHERE id=?", (event_id,))

    def event(self, event_id: str) -> Event | None:
        r = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._row_event(r) if r else None

    def events_since(self, ts: float) -> list[Event]:
        rows = self.db.execute("SELECT * FROM events WHERE last_updated >= ?", (ts,)).fetchall()
        return [self._row_event(r) for r in rows]

    def events_by_ids(self, ids: Iterable[str]) -> dict[str, Event]:
        ids = list(set(ids))
        out: dict[str, Event] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = "SELECT * FROM events WHERE id IN (%s)" % ",".join("?" * len(chunk))
            for r in self.db.execute(q, chunk):
                out[r["id"]] = self._row_event(r)
        return out

    def _row_event(self, r) -> Event:
        e = Event(id=r["id"], centroid=_from_blob(r["centroid"]), n_articles=r["n_articles"], outlets=r["outlets"],
                  first_seen=r["first_seen"], last_updated=r["last_updated"], rep_article=r["rep_article"],
                  tags=json.loads(r["tags"] or "{}"), summary=json.loads(r["summary"]) if r["summary"] else None,
                  summary_version=r["summary_version"] or 0)
        return e

    def event_feeds(self, event_ids: Iterable[str]) -> dict[str, set[str]]:
        ids = list(set(event_ids))
        out: dict[str, set[str]] = {i: set() for i in ids}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ("SELECT a.event_id AS e, f.feed_key AS k FROM articles a JOIN article_feeds f ON f.article_id = a.id "
                 "WHERE a.event_id IN (%s)" % ",".join("?" * len(chunk)))
            for r in self.db.execute(q, chunk):
                out[r["e"]].add(r["k"])
        return out

    def event_velocity(self, event_ids: Iterable[str], since: float) -> dict[str, int]:
        ids = list(set(event_ids))
        out = {i: 0 for i in ids}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ("SELECT event_id, COUNT(DISTINCT source) AS n FROM articles WHERE event_id IN (%s) AND first_seen >= ? "
                 "GROUP BY event_id" % ",".join("?" * len(chunk)))
            for r in self.db.execute(q, chunk + [since]):
                out[r["event_id"]] = r["n"]
        return out

    def latest_article_ts(self, event_id: str) -> float:
        r = self.db.execute("SELECT MAX(first_seen) AS t FROM articles WHERE event_id=?", (event_id,)).fetchone()
        return r["t"] or 0.0

    # ---- pruning ------------------------------------------------------------
    def prune(self, article_before: float, event_before: float) -> tuple[int, int]:
        old = [r["id"] for r in self.db.execute("SELECT id FROM articles WHERE first_seen < ?", (article_before,))]
        for i in range(0, len(old), 500):
            chunk = old[i:i + 500]
            ph = ",".join("?" * len(chunk))
            self.db.execute(f"DELETE FROM article_feeds WHERE article_id IN ({ph})", chunk)
            self.db.execute(f"DELETE FROM articles WHERE id IN ({ph})", chunk)
        n_ev = self.db.execute("DELETE FROM events WHERE last_updated < ?", (event_before,)).rowcount
        return len(old), n_ev

    # ---- impressions, sends, editions ----------------------------------------
    def log_impressions(self, rows: list[dict]) -> None:
        self.db.executemany(
            "INSERT INTO impressions(edition_id,reader,date,event_id,section,position,score,features,ranker_version,team,is_update,is_bubble,ts) "
            "VALUES (:edition_id,:reader,:date,:event_id,:section,:position,:score,:features,:ranker_version,:team,:is_update,:is_bubble,:ts)",
            rows)

    def last_shown(self, reader: str) -> dict[str, float]:
        rows = self.db.execute("SELECT event_id, MAX(ts) AS t FROM impressions WHERE reader=? GROUP BY event_id", (reader,))
        return {r["event_id"]: r["t"] for r in rows}

    def impressions_for(self, reader: str, since: float) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM impressions WHERE reader=? AND ts >= ?", (reader, since)).fetchall()

    def all_impressions(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM impressions").fetchall()

    def send_status(self, reader: str, date: str, kind: str) -> str | None:
        r = self.db.execute("SELECT status FROM sends WHERE reader=? AND date=? AND kind=?", (reader, date, kind)).fetchone()
        return r["status"] if r else None

    def mark_send(self, reader: str, date: str, kind: str, status: str, edition_id: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO sends(reader,date,kind,status,ts,edition_id) VALUES (?,?,?,?,?,?)",
                        (reader, date, kind, status, time.time(), edition_id))
        self.db.commit()

    def log_edition(self, row: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO editions(id,reader,date,kind,ts,stories,claims_total,claims_supported,numbers_removed,"
            "input_tokens,output_tokens,ms,notes) VALUES (:id,:reader,:date,:kind,:ts,:stories,:claims_total,:claims_supported,"
            ":numbers_removed,:input_tokens,:output_tokens,:ms,:notes)", row)

"""Event understanding: group articles into events, keep them up to date, compute signals.

Online clustering: each new article joins the nearest active event if cosine >= tau,
otherwise it starts a new event. A merge pass joins events whose centroids converged.
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass

import numpy as np

from .config import EVENT, EventParams
from .embed import Embedder, article_text, normalize
from .models import Article, Event
from .store import Store


@dataclass
class ClusterStats:
    new_articles: int = 0
    joined: int = 0
    new_events: int = 0
    merged: int = 0


def _event_id(article_id: str) -> str:
    return "e" + hashlib.sha1(article_id.encode()).hexdigest()[:11]


def importance(e: Event, now: float, cap: int = 10) -> float:
    """log(1 + outlets), normalized to [0, 1] at `cap` outlets, decayed by age (24h half-life)."""
    base = math.log1p(min(e.outlets, cap)) / math.log1p(cap)
    return base * 0.5 ** (e.age_h(now) / 24.0)


def ingest_articles(store: Store, embedder: Embedder, articles: list[Article], now: float | None = None,
                    params: EventParams = EVENT) -> ClusterStats:
    """Embed new articles and assign them to events. Known articles only get new feed links."""
    now = now or time.time()
    stats = ClusterStats()
    by_id: dict[str, Article] = {}
    for a in articles:  # same article can come from several feeds
        if a.id in by_id:
            by_id[a.id].feeds |= a.feeds
        else:
            by_id[a.id] = a
    known = store.known_article_ids(by_id)
    store.add_feed_links((a.id, f) for a in by_id.values() if a.id in known for f in a.feeds)
    fresh = sorted((a for a in by_id.values() if a.id not in known), key=lambda a: a.published)
    if not fresh:
        return stats

    vecs = embedder.embed([article_text(a.title, a.description) for a in fresh])
    for a, v in zip(fresh, vecs):
        a.emb = v

    active = store.events_since(now - params.join_window_h * 3600)
    ids = [e.id for e in active]
    mat = normalize(np.stack([e.centroid for e in active])) if active else np.zeros((0, vecs.shape[1]), dtype=np.float32)
    events = {e.id: e for e in active}
    sources: dict[str, set[str]] = {}

    for a in fresh:
        stats.new_articles += 1
        j = -1
        if len(ids):
            sims = mat @ a.emb
            j = int(np.argmax(sims))
            if sims[j] < params.tau:
                j = -1
        if j >= 0:
            e = events[ids[j]]
            e.centroid = normalize(e.centroid * e.n_articles + a.emb)
            e.n_articles += 1
            e.last_updated = max(e.last_updated, a.published, a.first_seen)
            mat[j] = e.centroid
            a.event_id = e.id
            stats.joined += 1
        else:
            e = Event(id=_event_id(a.id), centroid=a.emb.copy(), n_articles=1, outlets=1,
                      first_seen=a.published, last_updated=max(a.published, a.first_seen), rep_article=a.id)
            events[e.id] = e
            ids.append(e.id)
            mat = np.vstack([mat, e.centroid[None, :]])
            a.event_id = e.id
            stats.new_events += 1
        store.insert_article(a)
        sources.setdefault(a.event_id, set())

    # Recompute outlets + representative for touched events
    for eid in sources:
        _refresh_event(store, events[eid])
    stats.merged = merge_events(store, now, params)
    store.commit()
    return stats


def _refresh_event(store: Store, e: Event) -> None:
    arts = store.articles_for_event(e.id)
    if not arts:
        store.upsert_event(e)
        return
    e.n_articles = len(arts)
    e.outlets = len({a.source for a in arts})
    embs = [a.emb for a in arts if a.emb is not None]
    if embs:
        e.centroid = normalize(np.mean(np.stack(embs), axis=0))
        sims = [float(a.emb @ e.centroid) if a.emb is not None else -1 for a in arts]
        # Representative = closest to centroid, preferring articles that carry a description
        best = max(range(len(arts)), key=lambda i: (sims[i] + (0.02 if arts[i].description else 0)))
        e.rep_article = arts[best].id
    e.last_updated = max(max(a.first_seen for a in arts), max(a.published for a in arts))
    store.upsert_event(e)


def merge_events(store: Store, now: float, params: EventParams = EVENT) -> int:
    active = store.events_since(now - params.join_window_h * 3600)
    if len(active) < 2:
        return 0
    active.sort(key=lambda e: -e.n_articles)
    mat = normalize(np.stack([e.centroid for e in active]))
    sims = mat @ mat.T
    gone: set[int] = set()
    merged = 0
    for i in range(len(active)):
        if i in gone:
            continue
        for j in range(i + 1, len(active)):
            if j in gone or sims[i, j] < params.merge_tau:
                continue
            for a in store.articles_for_event(active[j].id):
                store.set_article_event(a.id, active[i].id)
            store.delete_event(active[j].id)
            gone.add(j)
            merged += 1
        if merged:
            _refresh_event(store, active[i])
    return merged


def active_events(store: Store, now: float, params: EventParams = EVENT) -> list[Event]:
    evs = store.events_since(now - params.active_window_h * 3600)
    feeds = store.event_feeds(e.id for e in evs)
    vel = store.event_velocity((e.id for e in evs), now - params.velocity_window_h * 3600)
    for e in evs:
        e.feeds = feeds.get(e.id, set())
        e.velocity = vel.get(e.id, 0)
    return evs


def prune(store: Store, now: float, params: EventParams = EVENT) -> tuple[int, int]:
    n = store.prune(now - params.article_retention_h * 3600, now - params.event_retention_days * 86400)
    store.commit()
    return n

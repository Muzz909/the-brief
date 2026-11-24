"""Plain data types shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Article:
    id: str
    url: str
    title: str
    source: str
    published: float            # unix seconds
    description: str = ""
    feeds: set[str] = field(default_factory=set)
    emb: np.ndarray | None = None
    event_id: str | None = None
    first_seen: float = 0.0


@dataclass
class Event:
    id: str
    centroid: np.ndarray
    n_articles: int
    outlets: int
    first_seen: float
    last_updated: float
    rep_article: str
    tags: dict = field(default_factory=dict)
    summary: dict | None = None
    summary_version: int = 0
    feeds: set[str] = field(default_factory=set)
    velocity: int = 0

    def age_h(self, now: float) -> float:
        return max(0.0, (now - self.last_updated) / 3600.0)


@dataclass
class Reader:
    token: str
    email: str
    name: str
    prefs: dict
    created_at: str = ""
    status: str = "active"


@dataclass
class Section:
    key: str            # e.g. "politics", "city0", "tech", "topic1"
    title: str
    feeds: list[str]    # feed keys that feed this section
    seed_text: str      # text whose embedding seeds the interest vector
    city: str | None = None


@dataclass
class Pick:
    event: Event
    section: str
    score: float
    features: dict
    explanation: str
    team: str = "v2"            # interleaving credit: "v2" | "base"
    is_update: bool = False
    is_bubble: bool = False


@dataclass
class EditionResult:
    edition_id: str
    reader: Reader
    date: str
    subject: str
    html: str
    text: str
    stats: dict[str, Any]
    picks: list[Pick]

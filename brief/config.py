"""Configuration: environment, constants and tunable parameters.

Every tunable number lives here so evals can sweep it and the decision log can point to it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key)
    return v if v not in (None, "") else default


# ---------------------------------------------------------------------------
# Secrets / endpoints (GitHub Actions secrets in production)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
APPS_SCRIPT_URL = env("APPS_SCRIPT_URL")          # v1 web app /exec URL (front door + reader DB)
BRIEF_API_KEY = env("BRIEF_API_KEY")              # shared secret between v1 and v2
GMAIL_ADDRESS = env("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = env("GMAIL_APP_PASSWORD")
PAGES_URL = (env("PAGES_URL") or "").rstrip("/")  # GitHub Pages base, hosts go.html (click tracking)
STATE_DIR = env("BRIEF_STATE_DIR", "./state")

# ---------------------------------------------------------------------------
# Models (routing: small model for tagging/verifying, strong model for writing)
# ---------------------------------------------------------------------------
WRITER_MODEL = env("BRIEF_WRITER_MODEL", "claude-sonnet-5-5")
FAST_MODEL = env("BRIEF_FAST_MODEL", "claude-haiku-5-5")
EMBED_MODEL = env("BRIEF_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_CACHE = env("BRIEF_EMBED_CACHE", os.path.expanduser("~/.cache/fastembed"))

# Optional $ per million tokens, for cost reporting. Left unset unless you fill them in
# from the current Anthropic pricing page; reports then show tokens only.
PRICE_PER_MTOK = {
    WRITER_MODEL: (float(env("PRICE_WRITER_IN", "0") or 0), float(env("PRICE_WRITER_OUT", "0") or 0)),
    FAST_MODEL: (float(env("PRICE_FAST_IN", "0") or 0), float(env("PRICE_FAST_OUT", "0") or 0)),
}


# ---------------------------------------------------------------------------
# Event understanding
# ---------------------------------------------------------------------------
@dataclass
class EventParams:
    tau: float = float(env("BRIEF_CLUSTER_TAU", "0.82"))      # cosine to join an event (tune with labels)
    merge_tau: float = float(env("BRIEF_MERGE_TAU", "0.88"))  # cosine to merge two events
    join_window_h: float = 48.0                                # an event accepts articles for 48h
    active_window_h: float = 72.0                              # events older than this are not candidates
    article_retention_h: float = 96.0                          # prune raw articles after 4 days
    event_retention_days: float = 35.0                         # keep event centroids for feedback vectors
    velocity_window_h: float = 6.0


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
@dataclass
class RankParams:
    weights: dict = field(default_factory=lambda: {
        "relevance": 0.35, "importance": 0.25, "freshness": 0.20,
        "affinity": 0.10, "novelty": 0.10, "dislike": 0.30,
    })
    freshness_half_life_h: float = 12.0
    importance_cap_outlets: int = 10
    candidates_per_reader: int = 200
    retrieval_min_sim: float = 0.55     # embedding retrieval floor (bge cosine)
    mmr_lambda: float = 0.7
    duplicate_sim: float = 0.86         # MMR: treat as same story above this
    min_slots: int = 1
    max_slots: int = 5
    total_section_slots: int = 14
    top_stories: int = 3
    bubble_max_sim: float = 0.62        # "outside your bubble": far from every interest vector
    feedback_days: int = 30
    like_step: float = 0.25             # Rocchio step toward liked events
    max_politics_outlet_share: float = 0.4


EVENT = EventParams()
RANK = RankParams()

# Interleaving experiment window (ISO dates, reader local). Outside it, v2 ranks alone.
EXPERIMENT_START = env("BRIEF_EXPERIMENT_START", "2026-11-25")
EXPERIMENT_END = env("BRIEF_EXPERIMENT_END", "2026-12-02")
RANKER_VERSION = "v2.0"

DEFAULT_TZ = "Asia/Dubai"
HTTP_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; TheBrief/2.0; +https://github.com)"


# ---------------------------------------------------------------------------
# Catalogues (must match the v1 settings page)
# ---------------------------------------------------------------------------
INDICES = {
    "^BSESN": "Sensex", "^NSEI": "Nifty 50", "^NSEBANK": "Nifty Bank", "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq", "^DJI": "Dow Jones", "^TASI.SR": "Tadawul (TASI)", "^FTSE": "FTSE 100",
    "^N225": "Nikkei 225", "^HSI": "Hang Seng", "BZ=F": "Brent crude", "BTC-USD": "Bitcoin",
}

POLITICS = {
    "india": {"label": "India", "q": "India politics OR government OR parliament", "ed": "IN", "seed": "Indian politics, government and parliament"},
    "gulf": {"label": "UAE & Gulf", "q": "UAE OR GCC government OR policy", "ed": "AE", "seed": "UAE and Gulf government policy"},
    "saudi": {"label": "Saudi Arabia", "q": '"Saudi Arabia" government OR policy', "ed": "US", "seed": "Saudi Arabia government policy"},
    "pakistan": {"label": "Pakistan", "q": "Pakistan politics OR government", "ed": "PK", "seed": "Pakistan politics and government"},
    "us": {"label": "United States", "q": "US politics OR Congress OR White House", "ed": "US", "seed": "United States politics, Congress and the White House"},
    "uk": {"label": "United Kingdom", "q": "UK politics OR Westminster", "ed": "GB", "seed": "UK politics and Westminster"},
    "world": {"label": "World", "topic": "WORLD", "ed": "US", "seed": "major world news and international affairs"},
}

NEWS_EDITIONS = {
    "IN": ("en-IN", "IN", "IN:en"), "AE": ("en-AE", "AE", "AE:en"), "PK": ("en-PK", "PK", "PK:en"),
    "GB": ("en-GB", "GB", "GB:en"), "US": ("en-US", "US", "US:en"), "CA": ("en-CA", "CA", "CA:en"),
    "AU": ("en-AU", "AU", "AU:en"), "SG": ("en-SG", "SG", "SG:en"),
}

GOLD_UNITS = {"g": ("per gram", 1.0), "10g": ("per 10 g", 10.0), "tola": ("per tola", 11.6638038), "oz": ("per troy oz", 31.1034768)}
TROY_OZ_G = 31.1034768

DEFAULT_PREFS = {
    "tz": DEFAULT_TZ, "sendTime": "08:00", "cities": [],
    "sections": {"markets": True, "watchlist": True, "forex": True, "gold": True,
                 "politics": True, "cities": True, "tech": True, "topics": True},
    "indices": ["^BSESN", "^NSEI", "^GSPC"], "watchlist": [], "forex": ["AED-INR", "USD-INR"],
    "gold": {"currency": "AED", "unit": "g", "karats": ["24", "22"]},
    "politics": ["india", "gulf", "world"], "topics": [],
}

"""Two-stage recommender: retrieval → scoring → diversity, with a per-reader section bandit,
an exploration slot, explanations, a baseline ranker and team-draft interleaving.

Designed for very little data: interpretable features, hand-set weights tuned offline,
and every impression logged so a learned ranker is possible later.
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field

import numpy as np

from . import config
from .config import RANK, RankParams
from .embed import Embedder, normalize
from .events import importance
from .models import Event, Pick, Reader, Section


# ---------------------------------------------------------------------------
# Sections: what this reader asked for
# ---------------------------------------------------------------------------
def sections_for(reader: Reader, geos: dict[str, dict | None]) -> list[Section]:
    p = reader.prefs
    s = p.get("sections", {})
    out: list[Section] = []
    home_cc = None
    for c in p.get("cities", []):
        g = geos.get(c)
        if g and g.get("cc") in config.NEWS_EDITIONS:
            home_cc = g["cc"]
            break
    home_cc = home_cc or "US"
    if s.get("politics") and p.get("politics"):
        regions = [r for r in p["politics"] if r in config.POLITICS]
        out.append(Section("politics", "Politics", [f"politics:{r}" for r in regions],
                           "; ".join(config.POLITICS[r]["seed"] for r in regions)))
    if s.get("cities"):
        for i, c in enumerate(p.get("cities", [])[:3]):
            g = geos.get(c) or {}
            name = g.get("name") or c
            cc = g.get("cc") if g.get("cc") in config.NEWS_EDITIONS else "US"
            out.append(Section(f"city{i}", name, [f"city:{name}|{cc}"], f"local news from {name}", city=c))
    if s.get("tech"):
        out.append(Section("tech", "Tech & AI", ["tech:ai", "tech:topic"], "technology industry and artificial intelligence news"))
    if s.get("topics"):
        for i, t in enumerate(p.get("topics", [])[:5]):
            out.append(Section(f"topic{i}", t, [f"topic:{t}|{home_cc}"], f"news about {t}"))
    return out


def feed_keys_for(sections: list[Section]) -> set[str]:
    return {f for s in sections for f in s.feeds}


# ---------------------------------------------------------------------------
# Reader model: interest vectors, dislikes, bandit
# ---------------------------------------------------------------------------
@dataclass
class ReaderModel:
    vectors: dict[str, np.ndarray]
    negatives: np.ndarray | None
    alpha: dict[str, float]
    beta: dict[str, float]
    last_shown: dict[str, float]
    engaged: set[str] = field(default_factory=set)

    def affinity(self, key: str) -> float:
        a, b = self.alpha.get(key, 1.0), self.beta.get(key, 1.0)
        return a / (a + b)


def build_reader_model(reader: Reader, sections: list[Section], embedder: Embedder, store, feedback: list[dict],
                       clicks: list[dict], now: float, params: RankParams = RANK) -> ReaderModel:
    seeds = embedder.embed([s.seed_text for s in sections]) if sections else np.zeros((0, 1))
    vectors = {s.key: seeds[i] for i, s in enumerate(sections)}
    since = now - params.feedback_days * 86400

    mine_fb = [f for f in feedback if f.get("token") == reader.token and _ts(f.get("ts")) >= since]
    mine_clicks = [c for c in clicks if c.get("token") == reader.token and _ts(c.get("ts")) >= since]
    liked = {f["storyKey"]: _ts(f.get("ts")) for f in mine_fb if f.get("vote") == "up"}
    disliked = {f["storyKey"] for f in mine_fb if f.get("vote") == "down"}
    liked.update({c["storyKey"]: _ts(c.get("ts")) for c in mine_clicks if c["storyKey"] not in disliked})
    evs = store.events_by_ids(list(liked) + list(disliked))

    # Rocchio-style update: move each section's vector toward liked events that resemble it
    if sections and liked:
        keys = list(vectors)
        mat = normalize(np.stack([vectors[k] for k in keys]))
        sums = {k: np.zeros_like(mat[0]) for k in keys}
        for eid, ts in liked.items():
            e = evs.get(eid)
            if e is None:
                continue
            j = int(np.argmax(mat @ e.centroid))
            sums[keys[j]] += e.centroid * 0.5 ** ((now - ts) / 86400 / 15)
        for k in keys:
            if np.any(sums[k]):
                vectors[k] = normalize(vectors[k] + params.like_step * sums[k])
    negs = [evs[e].centroid for e in disliked if e in evs]

    # Bandit: per section, success = the reader engaged with any story shown in that section in that edition
    engaged = set(liked)
    alpha = {s.key: 1.0 for s in sections}
    beta = {s.key: 1.0 for s in sections}
    shown: dict[tuple[str, str], bool] = {}
    for r in store.impressions_for(reader.token, since):
        k = (r["edition_id"], r["section"])
        shown[k] = shown.get(k, False) or (r["event_id"] in engaged)
    for (_, sec), hit in shown.items():
        if sec in alpha:
            if hit:
                alpha[sec] += 1
            else:
                beta[sec] += 1
    return ReaderModel(vectors=vectors, negatives=normalize(np.stack(negs)) if negs else None,
                       alpha=alpha, beta=beta, last_shown=store.last_shown(reader.token), engaged=engaged)


def _ts(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v) / (1000.0 if v > 1e11 else 1.0)
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def allocate_slots(sections: list[Section], model: ReaderModel, rng: np.random.Generator,
                   available: dict[str, int], params: RankParams = RANK) -> dict[str, int]:
    """Thompson sampling: draw θ ~ Beta(α, β) per section, split the slot budget in proportion."""
    keys = [s.key for s in sections if available.get(s.key, 0) > 0]
    if not keys:
        return {}
    theta = {k: rng.beta(model.alpha.get(k, 1), model.beta.get(k, 1)) for k in keys}
    total = sum(theta.values()) or 1.0
    slots = {}
    for k in keys:
        raw = params.total_section_slots * theta[k] / total
        slots[k] = int(min(params.max_slots, max(params.min_slots, round(raw)), available[k]))
    return slots


# ---------------------------------------------------------------------------
# Candidates + features
# ---------------------------------------------------------------------------
def _scale(cos: float, lo: float = 0.5, hi: float = 0.9) -> float:
    return float(min(1.0, max(0.0, (cos - lo) / (hi - lo))))


@dataclass
class Candidate:
    event: Event
    section: str
    features: dict
    score: float
    is_update: bool
    source: str = ""          # representative outlet
    from_feed: bool = False


def candidates(events: list[Event], sections: list[Section], model: ReaderModel, now: float,
               rep_source: dict[str, str], params: RankParams = RANK) -> list[Candidate]:
    if not sections:
        return []
    keys = [s.key for s in sections]
    vmat = normalize(np.stack([model.vectors[k] for k in keys]))
    feed_to_section = {}
    for s in sections:
        for f in s.feeds:
            feed_to_section.setdefault(f, s.key)
    out: list[Candidate] = []
    for e in events:
        if e.tags.get("type") == "lowvalue":
            continue
        last = model.last_shown.get(e.id)
        is_update = False
        if last is not None:
            if e.last_updated <= last + 60:
                continue  # story memory: already seen, nothing new
            is_update = True
        sims = vmat @ e.centroid
        feed_hits = [feed_to_section[f] for f in e.feeds if f in feed_to_section]
        if feed_hits:
            sec = max(set(feed_hits), key=lambda k: sims[keys.index(k)])
            rel = max(_scale(float(sims[keys.index(sec)])), 0.6)
            from_feed = True
        else:
            j = int(np.argmax(sims))
            if sims[j] < params.retrieval_min_sim:
                continue
            sec, rel, from_feed = keys[j], _scale(float(sims[j])), False
        dislike = 0.0
        if model.negatives is not None:
            dislike = _scale(float(np.max(model.negatives @ e.centroid)))
        f = {
            "relevance": rel,
            "importance": importance(e, now, params.importance_cap_outlets),
            "freshness": 0.5 ** (e.age_h(now) / params.freshness_half_life_h),
            "affinity": model.affinity(sec),
            "novelty": 0.6 if is_update else 1.0,
            "dislike": dislike,
        }
        w = params.weights
        score = sum(w[k] * f[k] for k in ("relevance", "importance", "freshness", "affinity", "novelty")) - w["dislike"] * dislike
        if e.tags.get("type") == "opinion":
            score -= 0.1
        out.append(Candidate(e, sec, f, score, is_update, rep_source.get(e.id, ""), from_feed))
    out.sort(key=lambda c: -c.score)
    return out[: params.candidates_per_reader]


# ---------------------------------------------------------------------------
# Selection with diversity
# ---------------------------------------------------------------------------
def mmr(cands: list[Candidate], k: int, chosen_vecs: list[np.ndarray], params: RankParams = RANK,
        max_per_source: int | None = None) -> list[Candidate]:
    picked: list[Candidate] = []
    pool = list(cands)
    per_src: dict[str, int] = {}
    while pool and len(picked) < k:
        best, best_val = None, -1e9
        for c in pool:
            sim = max((float(v @ c.event.centroid) for v in chosen_vecs), default=0.0)
            if sim >= params.duplicate_sim:
                continue
            if max_per_source and c.source and per_src.get(c.source, 0) >= max_per_source:
                continue
            val = params.mmr_lambda * c.score - (1 - params.mmr_lambda) * max(0.0, sim)
            if val > best_val:
                best, best_val = c, val
        if best is None:
            break
        pool.remove(best)
        picked.append(best)
        chosen_vecs.append(best.event.centroid)
        per_src[best.source] = per_src.get(best.source, 0) + 1
    return picked


def explain(c: Candidate, section_title: str) -> str:
    f = c.features
    w = RANK.weights
    parts = []
    contrib = sorted(((w[k] * f[k], k) for k in ("relevance", "importance", "freshness")), reverse=True)
    for _, k in contrib[:2]:
        if k == "relevance":
            parts.append(f"you follow {section_title}" if c.from_feed else f"close to your interest in {section_title}")
        elif k == "importance":
            n = c.event.outlets
            parts.append(f"reported by {n} outlet{'s' if n != 1 else ''}")
        elif k == "freshness":
            h = max(1, round(c.event.age_h(time.time()) or 1))
            parts.append("breaking in the last few hours" if h <= 6 else "from the last day")
    if c.is_update:
        parts.insert(0, "new developments since you last saw it")
    return "Because " + " · ".join(parts[:2])


@dataclass
class Plan:
    top: list[Pick]
    sections: dict[str, list[Pick]]
    bubble: Pick | None
    slots: dict[str, int]
    experiment: bool


def _to_pick(c: Candidate, title: str, team: str = "v2") -> Pick:
    return Pick(event=c.event, section=c.section, score=c.score, features=c.features,
                explanation=explain(c, title), team=team, is_update=c.is_update)


def baseline_order(cands: list[Candidate], now: float) -> list[Candidate]:
    """v1-style control: no personalization, newest and most-covered first; feed stories only."""
    pool = [c for c in cands if c.from_feed]
    return sorted(pool, key=lambda c: -(0.5 * c.features["importance"] + 0.5 * c.features["freshness"]))


def interleave(a: list[Candidate], b: list[Candidate], k: int, rng: np.random.Generator) -> list[tuple[Candidate, str]]:
    """Team-draft interleaving (Radlinski et al., 2008): unbiased within-reader comparison of two rankers."""
    out: list[tuple[Candidate, str]] = []
    used: set[str] = set()
    na = nb = ia = ib = 0

    def skip(lst: list[Candidate], idx: int) -> int:
        while idx < len(lst) and lst[idx].event.id in used:
            idx += 1
        return idx

    while len(out) < k:
        ia, ib = skip(a, ia), skip(b, ib)
        a_left, b_left = ia < len(a), ib < len(b)
        if not a_left and not b_left:
            break
        a_turn = na < nb or (na == nb and rng.random() < 0.5)
        if a_turn and not a_left:
            a_turn = False
        elif not a_turn and not b_left:
            a_turn = True
        if a_turn:
            c, team = a[ia], "v2"
            ia += 1
            na += 1
        else:
            c, team = b[ib], "base"
            ib += 1
            nb += 1
        used.add(c.event.id)
        out.append((c, team))
    return out


def in_experiment(date: str) -> bool:
    return config.EXPERIMENT_START <= date <= config.EXPERIMENT_END


def rank(reader: Reader, sections: list[Section], events: list[Event], model: ReaderModel, now: float, date: str,
         rep_source: dict[str, str], params: RankParams = RANK, experiment: bool | None = None) -> Plan:
    seed = int(hashlib.sha1(f"{reader.token}|{date}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    experiment = in_experiment(date) if experiment is None else experiment
    titles = {s.key: s.title for s in sections}
    cands = candidates(events, sections, model, now, rep_source, params)
    by_sec: dict[str, list[Candidate]] = {s.key: [] for s in sections}
    for c in cands:
        by_sec[c.section].append(c)

    chosen_vecs: list[np.ndarray] = []
    # --- Top stories ---
    v2_top = mmr(cands, params.top_stories * 3, list(chosen_vecs), params)
    if experiment:
        base_top = sorted(baseline_order(cands, now), key=lambda c: -c.event.outlets)
        top_pairs = interleave(v2_top, base_top, params.top_stories, rng)
    else:
        top_pairs = [(c, "v2") for c in v2_top[: params.top_stories]]
    top = []
    for c, team in top_pairs:
        top.append(_to_pick(c, titles[c.section], team))
        chosen_vecs.append(c.event.centroid)
    used = {p.event.id for p in top}

    # --- Sections (bandit decides slot counts) ---
    avail = {k: len([c for c in v if c.event.id not in used]) for k, v in by_sec.items()}
    slots = allocate_slots(sections, model, rng, avail, params)
    out_secs: dict[str, list[Pick]] = {}
    for s in sections:
        k = slots.get(s.key, 0)
        pool = [c for c in by_sec[s.key] if c.event.id not in used]
        if not k or not pool:
            out_secs[s.key] = []
            continue
        cap = max(1, math.floor(params.max_politics_outlet_share * k)) if s.key == "politics" else None
        v2_list = mmr(pool, k * 2, list(chosen_vecs), params, cap)
        if experiment:
            pairs = interleave(v2_list, baseline_order(pool, now), k, rng)
        else:
            pairs = [(c, "v2") for c in v2_list[:k]]
        picks = []
        for c, team in pairs:
            if c.event.id in used:
                continue
            picks.append(_to_pick(c, s.title, team))
            used.add(c.event.id)
            chosen_vecs.append(c.event.centroid)
        out_secs[s.key] = picks

    # --- Outside your bubble (exploration) ---
    bubble = None
    if sections:
        vmat = normalize(np.stack([model.vectors[s.key] for s in sections]))
        pool = [e for e in events if e.id not in used and e.id not in model.last_shown and e.outlets >= 3
                and e.tags.get("type") not in ("lowvalue", "opinion")
                and float(np.max(vmat @ e.centroid)) < params.bubble_max_sim]
        if pool:
            e = max(pool, key=lambda e: importance(e, now) * 0.5 ** (e.age_h(now) / 12))
            bubble = Pick(event=e, section="bubble", score=importance(e, now), features={"importance": importance(e, now)},
                          explanation=f"Outside your usual interests: reported by {e.outlets} outlets", is_bubble=True)
    return Plan(top=top, sections=out_secs, bubble=bubble, slots=slots, experiment=experiment)

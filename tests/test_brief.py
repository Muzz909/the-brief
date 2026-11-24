"""Offline test suite: parsing, clustering, ranking, grounding, interleaving, delivery and idempotency."""
from __future__ import annotations

import time

import numpy as np
import pytest

from brief import config, http
from brief.embed import HashEmbedder, normalize
from brief.events import ingest_articles, importance
from brief.experiment import sign_test_p
from brief.feeds import news
from brief.llm import FakeLLM, parse_json
from brief.models import Event
from brief.pipeline import Pipeline, is_due
from brief.ranker import interleave, allocate_slots, ReaderModel, Candidate
from brief.send import MemorySender
from brief.store import Store
from brief.writer import is_grounded, number_set

from . import fakes


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    http.clear_cache()
    monkeypatch.setattr(http, "TRANSPORT", fakes.transport)
    # Hash embeddings have a different similarity scale from bge: tune thresholds for tests
    monkeypatch.setattr(config.EVENT, "tau", 0.5)
    monkeypatch.setattr(config.EVENT, "merge_tau", 0.8)
    monkeypatch.setattr(config.RANK, "retrieval_min_sim", 0.35)
    monkeypatch.setattr(config.RANK, "bubble_max_sim", 0.3)
    monkeypatch.setattr(config, "APPS_SCRIPT_URL", "https://script.google.com/macros/s/TEST/exec")
    monkeypatch.setattr(config, "PAGES_URL", "https://muzz909.github.io/the-brief")
    yield


@pytest.fixture
def pipe(tmp_path):
    store = Store(str(tmp_path / "brief.db"))
    p = Pipeline(store, HashEmbedder(), FakeLLM(fakes.llm_handler), MemorySender(), client=None,
                 now_fn=lambda: fakes.NOW)
    yield p
    store.close()


# ---------------------------------------------------------------------------
def test_rss_parser_strips_outlet_and_redundant_description():
    xml = fakes._rss([("Singapore air quality improves", "Times of India", 2)])
    arts = news.parse_rss(xml, "city:Singapore|IN", now=fakes.NOW)
    assert len(arts) == 1
    a = arts[0]
    assert a.title == "Singapore air quality improves" and a.source == "Times of India"
    assert a.description == ""  # Google's description only repeats the title
    assert a.feeds == {"city:Singapore|IN"}


def test_rss_parser_drops_old_items():
    xml = fakes._rss([("Old news item", "X", 100)])
    assert news.parse_rss(xml, "k", now=fakes.NOW, max_age_h=72) == []


def test_clustering_groups_same_event_across_outlets(pipe):
    xml = fakes._rss(fakes.FEEDS["world"])
    arts = news.parse_rss(xml, "politics:world", now=fakes.NOW)
    st = ingest_articles(pipe.store, pipe.embedder, arts, fakes.NOW)
    events = pipe.store.events_since(0)
    climate = [e for e in events if "climate" in pipe.store.article(e.rep_article).title.lower()]
    volcano = [e for e in events if "volcano" in pipe.store.article(e.rep_article).title.lower()]
    assert len(climate) == 1 and climate[0].outlets == 3
    assert len(volcano) == 1 and volcano[0].outlets == 3
    assert st.new_articles == 7 and len(events) == 3


def test_reingest_is_idempotent_and_adds_feed_links(pipe):
    arts = news.parse_rss(fakes._rss(fakes.FEEDS["india"]), "politics:india", now=fakes.NOW)
    ingest_articles(pipe.store, pipe.embedder, arts, fakes.NOW)
    n = len(pipe.store.events_since(0))
    again = news.parse_rss(fakes._rss(fakes.FEEDS["india"]), "topic:parliament|IN", now=fakes.NOW)
    st = ingest_articles(pipe.store, pipe.embedder, again, fakes.NOW)
    assert st.new_articles == 0 and len(pipe.store.events_since(0)) == n
    feeds = pipe.store.event_feeds(e.id for e in pipe.store.events_since(0))
    assert all({"politics:india", "topic:parliament|IN"} <= f for f in feeds.values())


def test_importance_grows_with_outlets_and_decays_with_age():
    c = normalize(np.ones(4))
    fresh_many = Event("a", c, 5, 5, fakes.NOW, fakes.NOW, "x")
    fresh_one = Event("b", c, 1, 1, fakes.NOW, fakes.NOW, "x")
    old_many = Event("c", c, 5, 5, fakes.NOW - 86400 * 2, fakes.NOW - 86400 * 2, "x")
    assert importance(fresh_many, fakes.NOW) > importance(fresh_one, fakes.NOW)
    assert importance(fresh_many, fakes.NOW) > importance(old_many, fakes.NOW)


# ---------------------------------------------------------------------------
def test_numbers_check():
    allowed = number_set(["Sensex 79,876.12 (-0.55%)", "16 bills"])
    assert is_grounded("Sensex fell 0.55% to 79,876.12", allowed)
    assert is_grounded("down about 0.6%", allowed)                  # rounding of a real number
    assert not is_grounded("Sensex fell 3.2%", allowed)             # invented
    assert not is_grounded("40 bills", allowed)
    assert is_grounded("three ministers and 2 parties", allowed)    # small counts allowed


def test_parse_json_handles_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json("nope") is None


# ---------------------------------------------------------------------------
def _cand(eid, score):
    e = Event(eid, normalize(np.random.default_rng(hash(eid) % 2**32).normal(size=8)), 1, 1, 0, 0, "x")
    return Candidate(e, "s", {}, score, False)


def test_team_draft_interleaving_is_balanced_and_deduplicated():
    a = [_cand(f"a{i}", 1) for i in range(5)] + [_cand("shared", 1)]
    b = [_cand("shared", 1)] + [_cand(f"b{i}", 1) for i in range(5)]
    rng = np.random.default_rng(1)
    out = interleave(a, b, 6, rng)
    ids = [c.event.id for c, _ in out]
    teams = [t for _, t in out]
    assert len(ids) == len(set(ids)) == 6
    assert abs(teams.count("v2") - teams.count("base")) <= 1


def test_interleaving_falls_back_when_one_list_is_exhausted():
    out = interleave([_cand("a0", 1)], [_cand(f"b{i}", 1) for i in range(4)], 4, np.random.default_rng(0))
    assert len(out) == 4


def test_bandit_respects_bounds():
    from brief.models import Section
    secs = [Section(k, k, [], "") for k in ("politics", "city0", "tech")]
    model = ReaderModel(vectors={}, negatives=None, alpha={"politics": 30, "city0": 1, "tech": 1},
                        beta={"politics": 1, "city0": 30, "tech": 30}, last_shown={})
    slots = allocate_slots(secs, model, np.random.default_rng(0), {"politics": 10, "city0": 10, "tech": 1})
    assert slots["politics"] == config.RANK.max_slots
    assert slots["city0"] >= config.RANK.min_slots and slots["tech"] == 1


def test_sign_test():
    assert sign_test_p(9, 1) < 0.05
    assert sign_test_p(5, 5) == 1.0


# ---------------------------------------------------------------------------
def test_end_to_end_edition(pipe):
    data = fakes.reader_data(with_other=True)
    report = pipe.run(data, tokens=[data.readers[0].token], kind="daily", force_ingest=True)
    assert report["failed"] == [], report["failed"]
    assert len(report["sent"]) == 1
    msg = pipe.sender.sent[0]
    html = msg["html"]

    # Numbers straight from data
    assert "79,876.12" in html and "25.26 INR" in html and "AED 427.90" in html
    # Events, not articles: the climate story appears once, with its outlets counted
    assert html.count("UN climate talks enter final week") <= 2  # headline + maybe preheader
    assert "3 outlets" in html
    # Grounding: invented number and unsupported claim never reach the reader
    assert "40 million" not in html
    assert "collapsed" not in html
    assert "3.2%" not in html
    assert "Sensex closed at 79,876.12, down 0.55%." in html
    # Personalization features
    assert "Why it matters to you" in html
    assert "Because " in html                       # explanations
    assert "Outside your bubble" in html            # exploration slot
    assert "horoscope" not in html.lower()          # low-value filtered by tags
    assert "Singapore" in html and "31° / 22°C" in html
    # Tracking + feedback links
    assert "muzz909.github.io/the-brief/go.html#" in html and "a=fb" in html
    # Logged for evaluation
    imps = pipe.store.all_impressions()
    assert len(imps) == report["sent"][0]["stories"]
    assert {r["team"] for r in imps} <= {"v2", "base"}
    ed = pipe.store.db.execute("SELECT * FROM editions").fetchone()
    assert ed["claims_supported"] < ed["claims_total"]   # the guardrails removed something


def test_shared_summaries_are_cached_across_readers(pipe):
    data = fakes.reader_data()
    pipe.run(data, kind="preview", force_ingest=True)
    calls_first = sum(1 for m, s, u in pipe.llm.calls if s.startswith("You write event summaries"))
    pipe.run(data, kind="preview")
    calls_second = sum(1 for m, s, u in pipe.llm.calls if s.startswith("You write event summaries")) - calls_first
    assert calls_first > 0
    assert calls_second < calls_first  # story memory + cache: no re-summarizing unchanged events


def test_daily_send_is_idempotent(pipe):
    data = fakes.reader_data()
    pipe.run(data, tokens=[data.readers[0].token], kind="daily", force_ingest=True)
    r2 = pipe.run(data, tokens=[data.readers[0].token], kind="daily")
    assert len(pipe.sender.sent) == 1 and r2["skipped"]


def test_story_memory_suppresses_seen_events(pipe):
    data = fakes.reader_data()
    pipe.run(data, kind="preview", force_ingest=True)
    first = {r["event_id"] for r in pipe.store.all_impressions()}
    pipe.run(data, kind="preview")
    rows = pipe.store.all_impressions()
    second_edition = rows[-1]["edition_id"]
    second = {r["event_id"] for r in rows if r["edition_id"] == second_edition}
    assert not (first & second), "already-seen events with nothing new must not repeat"


def test_not_due_before_send_time():
    data = fakes.reader_data(send_time="23:59")
    r = data.readers[0]
    import datetime as dt
    from zoneinfo import ZoneInfo
    t = dt.datetime(2026, 11, 24, 7, 0, tzinfo=ZoneInfo("Asia/Dubai")).timestamp()
    assert not is_due(r, t)
    r.prefs["sendTime"] = "06:30"
    assert is_due(r, t)


def test_model_outage_still_sends_headlines(pipe, monkeypatch):
    def broken(model, system, user):
        raise RuntimeError("overloaded")
    pipe.llm.handler = broken
    pipe.writer.llm = pipe.llm
    report = pipe.run(fakes.reader_data(), kind="preview", force_ingest=True)
    assert report["failed"] == [] and len(report["sent"]) == 1
    html = pipe.sender.sent[0]["html"]
    assert "UN climate talks enter final week" in html      # headlines still go out
    assert "Why it matters to you" not in html               # no unchecked AI text
    assert "79,876.12" in html                               # numbers never depended on the model


def test_safety_net_waits_for_handover(pipe):
    data = fakes.reader_data(send_time="00:00")
    data.v2_mode = False
    import datetime as dt
    from zoneinfo import ZoneInfo
    pipe.now_fn = lambda: dt.datetime(2026, 11, 24, 2, 0, tzinfo=ZoneInfo("Asia/Dubai")).timestamp()
    r = pipe.run(data, kind="daily", force_ingest=True)
    assert r["sent"] == [] and pipe.sender.sent == []      # v1 still owns daily sends
    data.v2_mode = True
    r = pipe.run(data, kind="daily")
    assert len(r["sent"]) == 1                              # after hand-over, the hourly run sends what's due

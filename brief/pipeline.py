"""Orchestration: ingest (shared) → per-reader rank, write, render, send, log."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
import uuid
from zoneinfo import ZoneInfo

from . import config, events as ev, http
from .embed import Embedder
from .feeds import markets, news
from .llm import LLM
from .models import EditionResult, Reader
from .ranker import build_reader_model, feed_keys_for, rank, sections_for
from .readers import ReaderData
from .render import Links, market_text, render
from .store import Store
from .writer import GroundingStats, Writer

log = logging.getLogger("brief")


def local_now(reader: Reader, now: float) -> dt.datetime:
    try:
        tz = ZoneInfo(reader.prefs.get("tz") or config.DEFAULT_TZ)
    except Exception:
        tz = ZoneInfo(config.DEFAULT_TZ)
    return dt.datetime.fromtimestamp(now, tz)


def is_due(reader: Reader, now: float, window_h: float = 6) -> bool:
    t = local_now(reader, now)
    hh, mm = (reader.prefs.get("sendTime") or "08:00").split(":")
    send_at = t.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    return send_at <= t <= send_at + dt.timedelta(hours=window_h)


class Pipeline:
    def __init__(self, store: Store, embedder: Embedder, llm: LLM, sender, client=None, now_fn=time.time,
                 out_dir: str | None = None):
        self.store, self.embedder, self.llm, self.sender, self.client = store, embedder, llm, sender, client
        self.writer = Writer(llm)
        self.now_fn = now_fn
        self.out_dir = out_dir

    # ---- geography --------------------------------------------------------
    def geos_for(self, readers: list[Reader]) -> dict[str, dict | None]:
        cities = {c for r in readers for c in r.prefs.get("cities", [])[:3]}
        return {c: markets.geocode(c, self.store) for c in cities}

    # ---- shared work: ingest + understand -----------------------------------
    def ingest(self, readers: list[Reader], force: bool = False) -> dict:
        now = self.now_fn()
        last = self.store.get_meta("last_ingest", 0)
        if not force and now - last < 20 * 60:
            return {"skipped": True, "minutes_since_last": round((now - last) / 60, 1)}
        geos = self.geos_for(readers)
        keys = sorted({k for r in readers for k in feed_keys_for(sections_for(r, geos))})
        urls = [news.feed_urls(k) for k in keys]
        resps = http.get_many([u for u, _ in urls])
        arts, feed_counts, failed = [], {}, []
        for k, (u, wide), r in zip(keys, urls, resps):
            items = news.parse_rss(r.text, k, now) if r is not None and r.status == 200 else []
            if r is None or r.status != 200:
                failed.append(k)
            if len(items) < 3 and wide:
                rw = http.get(wide)
                more = news.parse_rss(rw.text, k, now) if rw is not None and rw.status == 200 else []
                if len(more) > len(items):
                    items = more
            items = sorted(items, key=lambda a: -a.published)[:40]
            feed_counts[k] = len(items)
            arts.extend(items)
        stats = ev.ingest_articles(self.store, self.embedder, arts, now)
        pruned = ev.prune(self.store, now)
        tagged = self._tag_new(now)
        self.store.set_meta("last_ingest", now)
        self.store.commit()
        out = {"feeds": len(keys), "feeds_failed": failed, "articles_fetched": len(arts), "new_articles": stats.new_articles,
               "joined": stats.joined, "new_events": stats.new_events, "merged": stats.merged, "pruned": pruned, "tagged": tagged}
        log.info("ingest %s", json.dumps(out))
        return out

    def _tag_new(self, now: float, limit: int = 150) -> int:
        evs = [e for e in ev.active_events(self.store, now) if not e.tags]
        evs.sort(key=lambda e: -ev.importance(e, now))
        evs = evs[:limit]
        if not evs:
            return 0
        heads = []
        for e in evs:
            a = self.store.article(e.rep_article)
            heads.append((e.id, a.title if a else ""))
        try:
            tags = self.writer.tag(heads)
        except Exception as exc:  # tagging is an enhancement: never block the pipeline
            log.warning("tagging failed: %s", exc)
            return 0
        for e in evs:
            if e.id in tags:
                e.tags = tags[e.id]
                self.store.upsert_event(e)
        return len(tags)

    # ---- per reader ----------------------------------------------------------
    def build(self, reader: Reader, data: ReaderData, kind: str = "daily", experiment: bool | None = None) -> EditionResult:
        t0 = time.time()
        now = self.now_fn()
        local = local_now(reader, now)
        date = local.date().isoformat()
        edition_id = f"{date}-{reader.token[:8]}-{uuid.uuid4().hex[:6]}"
        p = reader.prefs
        geos = self.geos_for([reader])
        sections = sections_for(reader, geos)
        events = ev.active_events(self.store, now)
        reps = {e.id: self.store.article(e.rep_article) for e in events}
        rep_source = {k: (a.source if a else "") for k, a in reps.items()}
        model = build_reader_model(reader, sections, self.embedder, self.store, data.feedback, data.clicks, now)
        plan = rank(reader, sections, events, model, now, date, rep_source, experiment=experiment)

        gstats = GroundingStats()
        footer = {"total": 0, "supported": 0}
        titles = {s.key: s.title for s in sections}

        def item(pick) -> dict:
            e = pick.event
            arts = self.store.articles_for_event(e.id)
            rep = reps.get(e.id) or (arts[0] if arts else None)
            if pick.is_update:
                since = model.last_shown.get(e.id, 0)
                new_arts = [a for a in arts if a.first_seen > since] or arts
                sents, st = self.writer.summarize(e, new_arts, update=True)
                gstats.add(st)
                footer["total"] += st.sentences
                footer["supported"] += st.supported
            elif e.summary is not None and e.summary_version == e.n_articles:
                sents = e.summary.get("sentences", [])  # shared work: reused across readers
                footer["total"] += e.summary.get("stats", {}).get("sentences", len(sents))
                footer["supported"] += len(sents)
            else:
                sents, st = self.writer.summarize(e, arts)
                gstats.add(st)
                footer["total"] += st.sentences
                footer["supported"] += st.supported
                e.summary = {"sentences": sents, "stats": {"sentences": st.sentences, "supported": st.supported}}
                e.summary_version = e.n_articles
                self.store.upsert_event(e)
            others, seen = [], {rep.source if rep else ""}
            for a in sorted(arts, key=lambda a: -a.published):
                if a.source not in seen:
                    seen.add(a.source)
                    others.append({"source": a.source, "url": a.url})
            return {"event_id": e.id, "headline": rep.title if rep else "", "url": rep.url if rep else "",
                    "source": rep.source if rep else "", "summary": " ".join(s["text"] for s in sents),
                    "outlets": e.outlets, "others": others, "explanation": pick.explanation, "team": pick.team,
                    "section": pick.section, "section_title": titles.get(pick.section, "Outside your bubble"),
                    "is_update": pick.is_update}

        top = [item(pk) for pk in plan.top]
        sec_items = [{"key": s.key, "title": s.title, "items": [item(pk) for pk in plan.sections.get(s.key, [])],
                      "city": s.city} for s in sections]
        bubble = item(plan.bubble) if plan.bubble else None

        # Numbers (never through a model)
        s = p.get("sections", {})
        numbers = {}
        if s.get("markets") and p.get("indices"):
            numbers["markets"] = markets.quotes(p["indices"]) or None
        if s.get("watchlist") and p.get("watchlist"):
            numbers["watchlist"] = markets.quotes(p["watchlist"]) or None
        if s.get("forex") and p.get("forex"):
            numbers["forex"] = markets.forex(p["forex"])
        if s.get("gold"):
            numbers["gold"] = markets.gold(p.get("gold", {}))
        for k in ("markets", "watchlist"):
            if numbers.get(k) is not None and not numbers[k]["items"]:
                numbers[k] = None
        for sec in sec_items:
            if sec["city"] and geos.get(sec["city"]):
                sec["weather"] = markets.weather(geos[sec["city"]])

        # Personal lines
        mt = market_text(numbers)
        profile = (f"Name: {reader.name}\nCities: {', '.join(c for c in p.get('cities', [])) or 'none'}\n"
                   f"Sections: {', '.join(x.title for x in sections)}\n"
                   f"Markets tracked: {', '.join(config.INDICES.get(i, i) for i in p.get('indices', []))}; "
                   f"watchlist: {', '.join(p.get('watchlist', [])) or 'none'}; currencies: {', '.join(p.get('forex', [])) or 'none'}")
        briefing, why, personal_removed = self.writer.personal(
            profile, mt, [{"id": f"t{i}", "headline": t["headline"], "summary": t["summary"]} for i, t in enumerate(top)])
        for i, t in enumerate(top):
            t["why"] = why.get(f"t{i}", "")

        created = reader.created_at[:10] if reader.created_at else date
        try:
            edition_no = max(1, (local.date() - dt.date.fromisoformat(created)).days + 1)
        except ValueError:
            edition_no = 1
        considered = len(events)
        stats = {
            "events_considered": considered,
            "articles_considered": sum(e.n_articles for e in events),
            "claims_total": footer["total"],
            "claims_supported": footer["supported"],
            "new_claims_total": gstats.sentences,
            "new_claims_supported": gstats.supported,
            "numbers_removed": gstats.numbers_removed + personal_removed,
            "claims_removed": gstats.claims_removed,
            "grounding_log": gstats.log,
            "experiment": plan.experiment,
            "slots": plan.slots,
        }
        ed = {"links": Links(reader.token, edition_id), "prefs": p, "numbers": numbers, "name": reader.name,
              "cities": [s.title for s in sections if s.city], "date_label": f"{local.strftime('%A, %B')} {local.day}, {local.year}",
              "edition_no": edition_no, "briefing": briefing, "top": top, "sections": sec_items, "bubble": bubble,
              "stats": stats}
        html, text = render(ed)
        lead = top[0]["headline"] if top else "Your morning, on one page"
        subject = f"The Brief · {local.strftime('%a')} {local.day} {local.strftime('%b')}: {lead[:70]}"
        stats["ms"] = int((time.time() - t0) * 1000)
        res = EditionResult(edition_id=edition_id, reader=reader, date=date, subject=subject, html=html, text=text,
                            stats=stats, picks=plan.top + [pk for v in plan.sections.values() for pk in v] + ([plan.bubble] if plan.bubble else []))
        res.stats["items"] = {"top": top, "sections": sec_items, "bubble": bubble}
        return res

    def _log(self, res: EditionResult, kind: str) -> None:
        now = self.now_fn()
        rows = []
        for pos, pk in enumerate(res.picks):
            rows.append({"edition_id": res.edition_id, "reader": res.reader.token, "date": res.date, "event_id": pk.event.id,
                         "section": pk.section, "position": pos, "score": round(pk.score, 4),
                         "features": json.dumps({k: round(v, 4) for k, v in pk.features.items()}),
                         "ranker_version": config.RANKER_VERSION, "team": pk.team, "is_update": int(pk.is_update),
                         "is_bubble": int(pk.is_bubble), "ts": now})
        self.store.log_impressions(rows)
        tot = self.llm.totals
        self.store.log_edition({"id": res.edition_id, "reader": res.reader.token, "date": res.date, "kind": kind, "ts": now,
                                "stories": len(res.picks), "claims_total": res.stats["claims_total"],
                                "claims_supported": res.stats["claims_supported"], "numbers_removed": res.stats["numbers_removed"],
                                "input_tokens": tot["input"], "output_tokens": tot["output"], "ms": res.stats["ms"],
                                "notes": json.dumps({"experiment": res.stats["experiment"], "slots": res.stats["slots"],
                                                     "removed": res.stats["grounding_log"][:10]})})
        self.store.commit()

    # ---- the run -------------------------------------------------------------
    def run(self, data: ReaderData, tokens: list[str] | None = None, kind: str = "daily", force_ingest: bool = False) -> dict:
        now = self.now_fn()
        readers = data.readers
        if tokens:
            readers = [r for r in readers if r.token in set(tokens)]
        elif kind == "daily":
            # The hourly safety net only sends once v1 has handed daily editions over (V2_MODE=1),
            # otherwise readers would get both a v1 and a v2 edition.
            readers = [r for r in readers if is_due(r, now)] if data.v2_mode else []
        report = {"ingest": self.ingest(data.readers, force=force_ingest), "sent": [], "skipped": [], "failed": []}
        sent_items = []
        for r in readers:
            date = local_now(r, now).date().isoformat()
            if kind == "daily" and self.store.send_status(r.token, date, "daily") in ("sent", "sending"):
                report["skipped"].append(r.token[:8])
                continue
            try:
                if kind == "daily":
                    self.store.mark_send(r.token, date, "daily", "sending")
                res = self.build(r, data, kind)
                self.sender.send(r.email, res.subject, res.html, res.text)
                if kind == "daily":
                    self.store.mark_send(r.token, date, "daily", "sent", res.edition_id)
                self._log(res, kind)
                if self.out_dir:
                    os.makedirs(self.out_dir, exist_ok=True)
                    with open(os.path.join(self.out_dir, f"{res.edition_id}.html"), "w") as f:
                        f.write(res.html)
                report["sent"].append({"reader": r.token[:8], "edition": res.edition_id, "stories": len(res.picks),
                                       "claims": f"{res.stats['claims_supported']}/{res.stats['claims_total']}",
                                       "experiment": res.stats["experiment"], "ms": res.stats["ms"]})
                sent_items.append({"token": r.token, "date": date, "kind": kind})
            except Exception as exc:
                log.exception("edition failed for %s", r.token[:8])
                if kind == "daily":
                    self.store.mark_send(r.token, date, "daily", "failed")
                report["failed"].append({"reader": r.token[:8], "error": str(exc)[:300]})
        if self.client and sent_items:
            try:
                self.client.report_sent(sent_items)
            except Exception as exc:
                log.warning("report_sent failed: %s", exc)
        report["llm"] = {**self.llm.totals, "cost_usd": self.llm.cost_usd()}
        return report

"""Command line: python -m brief <command>

  ingest            fetch feeds, embed, cluster into events (shared work)
  due [tokens...]   send daily editions to readers who are due (or to the given tokens)
  preview TOKEN     build and send an edition now, ignoring the daily limit
  snapshot          freeze current articles + reader profiles for offline evals
  experiment        print the interleaving readout (wins / losses / sign test)
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import sys
import time

from . import config


def _components(need_mail: bool = True):
    from .embed import FastEmbedder
    from .llm import ClaudeLLM
    from .readers import AppsScriptClient
    from .send import GmailSender
    from .store import Store
    store = Store.open(config.STATE_DIR)
    return store, FastEmbedder(), ClaudeLLM(), (GmailSender() if need_mail else None), AppsScriptClient()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="brief")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    d = sub.add_parser("due")
    d.add_argument("tokens", nargs="*")
    pv = sub.add_parser("preview")
    pv.add_argument("tokens", nargs="+")
    sub.add_parser("snapshot")
    sub.add_parser("experiment")
    args = ap.parse_args(argv)

    from .pipeline import Pipeline

    if args.cmd == "experiment":
        from .experiment import readout
        from .readers import AppsScriptClient
        from .store import Store
        print(readout(Store.open(config.STATE_DIR), AppsScriptClient().fetch()))
        return 0

    store, embedder, llm, sender, client = _components(need_mail=args.cmd in ("due", "preview"))
    pipe = Pipeline(store, embedder, llm, sender, client, out_dir=os.environ.get("BRIEF_OUT_DIR", "out"))
    data = client.fetch()
    if args.cmd == "ingest":
        print(json.dumps(pipe.ingest(data.readers, force=True), indent=2))
    elif args.cmd == "due":
        report = pipe.run(data, tokens=args.tokens or None, kind="daily")
        print(json.dumps(report, indent=2, default=str))
        _summary(report)
        if report["failed"] and not report["sent"]:
            return 1
    elif args.cmd == "preview":
        report = pipe.run(data, tokens=args.tokens, kind="preview")
        print(json.dumps(report, indent=2, default=str))
        _summary(report)
    elif args.cmd == "snapshot":
        path = _snapshot(store, data)
        print("snapshot written:", path)
    store.close()
    return 0


def _snapshot(store, data) -> str:
    """Freeze the last 48h of articles + reader preferences (no emails) for reproducible evals."""
    since = time.time() - 48 * 3600
    rows = store.db.execute("SELECT * FROM articles WHERE first_seen >= ?", (since,)).fetchall()
    arts = []
    for r in rows:
        feeds = [x["feed_key"] for x in store.db.execute("SELECT feed_key FROM article_feeds WHERE article_id=?", (r["id"],))]
        arts.append({"id": r["id"], "url": r["url"], "title": r["title"], "source": r["source"], "published": r["published"],
                     "description": r["description"], "feeds": feeds, "event_id": r["event_id"]})
    profiles = [{"id": f"reader{i + 1}", "prefs": rd.prefs,
                 "geos": {c: store.get_meta("geo:" + c.lower().strip()) for c in rd.prefs.get("cities", [])}}
                for i, rd in enumerate(data.readers)]
    out_dir = os.environ.get("BRIEF_OUT_DIR", "out")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"snapshot-{time.strftime('%Y-%m-%d')}.json.gz")
    with gzip.open(path, "wt") as f:
        json.dump({"created": time.time(), "articles": arts, "profiles": profiles}, f)
    return path


def _summary(report: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a") as f:
        f.write("## The Brief run\n\n")
        ing = report.get("ingest", {})
        f.write(f"**Ingest:** {json.dumps(ing)}\n\n")
        f.write("| Reader | Edition | Stories | Claims kept | Experiment | ms |\n|---|---|---|---|---|---|\n")
        for s in report["sent"]:
            f.write(f"| {s['reader']} | {s['edition']} | {s['stories']} | {s['claims']} | {s['experiment']} | {s['ms']} |\n")
        for x in report["failed"]:
            f.write(f"| {x['reader']} | FAILED | {x['error']} | | | |\n")
        f.write(f"\n**LLM:** {json.dumps(report.get('llm'))}\n")


if __name__ == "__main__":
    sys.exit(main())

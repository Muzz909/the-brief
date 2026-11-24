"""Client for the v1 Apps Script web app, which stays the front door and reader database."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import requests

from . import config
from .models import Reader


@dataclass
class ReaderData:
    readers: list[Reader]
    feedback: list[dict] = field(default_factory=list)   # {token, storyKey, section, title, vote, ts}
    clicks: list[dict] = field(default_factory=list)     # {token, storyKey, team, section, ts}
    v2_mode: bool = True                                  # False until v1 hands daily sending over


def merge_prefs(p: dict) -> dict:
    out = json.loads(json.dumps(config.DEFAULT_PREFS))
    out.update(p or {})
    out["sections"] = {**config.DEFAULT_PREFS["sections"], **(p or {}).get("sections", {})}
    return out


class AppsScriptClient:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = url or config.APPS_SCRIPT_URL
        self.key = key or config.BRIEF_API_KEY
        if not self.url or not self.key:
            raise RuntimeError("APPS_SCRIPT_URL and BRIEF_API_KEY must be set")

    def fetch(self) -> ReaderData:
        r = requests.get(self.url, params={"a": "api", "k": self.key}, timeout=30)
        r.raise_for_status()
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError("Apps Script API error: " + str(j.get("error")))
        readers = [Reader(token=x["token"], email=x["email"], name=x.get("name") or "Reader",
                          prefs=merge_prefs(x.get("prefs") or {}), created_at=x.get("createdAt", ""),
                          status=x.get("status", "active"))
                   for x in j.get("readers", [])]
        return ReaderData(readers=readers, feedback=j.get("feedback", []), clicks=j.get("clicks", []),
                          v2_mode=bool(j.get("v2Mode")))

    def report_sent(self, items: list[dict]) -> None:
        """items: [{token, date, kind}] → v1 updates lastSentDate so it stops dispatching."""
        if not items:
            return
        requests.post(self.url, data=json.dumps({"k": self.key, "action": "sent", "items": items}),
                      headers={"Content-Type": "application/json"}, timeout=30)

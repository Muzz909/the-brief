"""Fake network, fake model and fake readers for offline tests. Headlines are made-up test data."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from email.utils import formatdate
from xml.sax.saxutils import escape

from brief.http import Response
from brief.models import Reader
from brief.readers import ReaderData, merge_prefs

NOW = time.time()


def _rss(items):
    out = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Google News</title>']
    for t, src, hrs in items:
        link = "https://news.example.com/" + hashlib.md5((t + src).encode()).hexdigest()[:10]
        desc = escape(f'<a href="{link}">{t}</a>&nbsp;&nbsp;<font color="#6f6f6f">{src}</font>')
        out.append(f"<item><title>{escape(t + ' - ' + src)}</title><link>{link}</link>"
                   f"<pubDate>{formatdate(NOW - hrs * 3600)}</pubDate><description>{desc}</description>"
                   f'<source url="https://{src.lower().replace(" ", "")}.com">{escape(src)}</source></item>')
    out.append("</channel></rss>")
    return "".join(out)


FEEDS = {
    "india": [("Parliament winter session opens next week with 16 bills listed", "The Hindu", 3),
              ("Parliament winter session to open next week, 16 bills listed", "Indian Express", 4),
              ("Election Commission announces schedule for 2 state bypolls", "NDTV", 6),
              ("Centre and states meet on GST rate rationalisation", "Mint", 9)],
    "gulf": [("UAE cabinet approves new federal budget framework", "Gulf News", 4),
             ("GCC ministers meet in Riyadh on regional trade corridor", "The National", 8)],
    "world": [("UN climate talks enter final week with finance deal unresolved", "Reuters", 2),
              ("UN climate talks final week: finance deal unresolved", "AP News", 3),
              ("Climate talks enter final week, finance deal still unresolved", "BBC", 3),
              ("European leaders gather for summit on defence spending", "Politico", 5),
              ("Volcano eruption in Iceland prompts evacuation of fishing town", "Reuters", 4),
              ("Iceland volcano eruption forces fishing town evacuation", "AP News", 5),
              ("Iceland evacuates fishing town after volcano eruption", "Guardian", 6)],
    "london": [("London opens new 12 km cycling track along the waterfront", "The National", 5),
                  ("London road closures announced for weekend marathon", "Khaleej Times", 7)],
    "singapore": [("Singapore municipal corporation approves 2 new flyover projects", "Free Press Journal", 6),
               ("Singapore air quality improves to moderate after rain", "Times of India", 9),
               ("Your weekly horoscope for Singapore readers", "Astro Daily", 2)],
    "toronto": [("Toronto Season announces dates for winter festival", "Arab News", 4),
               ("Toronto port records rise in container traffic", "Saudi Gazette", 11)],
    "ai": [("AI lab releases new open-weight model for developers", "The Verge", 3),
           ("New open-weight AI model released by lab for developers", "TechCrunch", 4),
           ("EU publishes guidance on AI Act compliance for startups", "Financial Times", 6)],
    "tech": [("Smartphone makers race to add on-device AI features", "Wired", 4)],
    "cricket": [("India announce squad for Test series against Australia", "ESPNcricinfo", 5)],
    "astronomy": [("Astronomers detect water vapour on distant exoplanet atmosphere", "Nature", 3),
                  ("Water vapour detected in distant exoplanet atmosphere, astronomers say", "BBC", 4),
                  ("Distant exoplanet atmosphere shows water vapour, astronomers report", "Space.com", 5)],
}

YAHOO = {"^BSESN": [80110.2, 80452.9, 79980.4, 80321.75, 79876.12], "^NSEI": [24210.5, 24301.1, 24180.2, 24290.85, 24151.3],
         "^GSPC": [5980.1, 6010.4, 6022.7, 5998.3, 6041.55]}
FX = {"latest": {"date": "2026-11-23", "aed": {"inr": 25.2561}, "usd": {"inr": 92.7535}, "xau": {"aed": 13309.2167}},
      "prev": {"date": "2026-11-22", "aed": {"inr": 25.2102}, "usd": {"inr": 92.5841}, "xau": {"aed": 13262.84}}}
GEO = {"london": ("London", "GB", 51.5, -0.12), "singapore": ("Singapore", "SG", 1.35, 103.8), "toronto": ("Toronto", "CA", 43.65, -79.38)}


def transport(url: str, timeout: float) -> Response:
    u = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(u.query)
    if "news.google.com" in url:
        if "/topic/WORLD" in u.path:
            return Response(200, _rss(FEEDS["world"]))
        if "/topic/TECHNOLOGY" in u.path:
            return Response(200, _rss(FEEDS["tech"]))
        q = qs.get("q", [""])[0].lower()
        for key, needle in (("india", "india politics"), ("gulf", "uae or gcc"), ("london", "london"), ("singapore", "singapore"),
                            ("toronto", "toronto"), ("ai", "artificial intelligence"), ("cricket", "cricket"), ("astronomy", "astronomy")):
            if needle in q:
                return Response(200, _rss(FEEDS[key]))
        return Response(200, _rss([]))
    if "finance.yahoo.com" in url:
        sym = urllib.parse.unquote(u.path.split("/chart/")[1])
        c = YAHOO.get(sym)
        if not c:
            return Response(404, "{}")
        ts = [int(NOW) - (4 - i) * 86400 - 3600 for i in range(5)]
        return Response(200, json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": c[-1], "regularMarketTime": ts[-1] + 1800, "currency": "INR"},
                                                               "timestamp": ts, "indicators": {"quote": [{"close": c}]}}]}}))
    m = re.search(r"currency-api@([^/]+)/v1/currencies/(\w+)\.json", url)
    if m:
        src = FX["latest"] if m.group(1) == "latest" else FX["prev"]
        return Response(200, json.dumps({"date": src["date"], m.group(2): src[m.group(2)]})) if m.group(2) in src else Response(404, "")
    if "geocoding-api" in url:
        name = qs["name"][0].lower()
        g = GEO.get(name)
        return Response(200, json.dumps({"results": [{"name": g[0], "country_code": g[1], "latitude": g[2], "longitude": g[3]}] if g else []}))
    if "api.open-meteo.com" in url:
        return Response(200, json.dumps({"daily": {"temperature_2m_max": [31.4], "temperature_2m_min": [21.8],
                                                   "precipitation_probability_max": [5], "weather_code": [1]}}))
    return Response(404, "")


# ---------------------------------------------------------------------------
def llm_handler(model: str, system: str, user: str) -> str:
    if system.startswith("You write event summaries") or "NEW articles" in system:
        srcs = re.findall(r"^\[(a\d+)\] ([^:]+): (.+?)(?: — .*)?$", user, re.M)
        sents = [{"text": f"{srcs[0][2]}, {srcs[0][1]} reports.", "sources": [srcs[0][0]]}]
        if len(srcs) > 1:
            sents.append({"text": f"{srcs[1][1]} also covered the story.", "sources": [srcs[1][0]]})
        if "cycling track" in user:
            sents.append({"text": "The track cost 40 million dirhams.", "sources": ["a1"]})       # invented number
        if "climate talks" in user.lower():
            sents.append({"text": "Talks collapsed entirely overnight.", "sources": ["a1"]})       # unsupported claim
        return json.dumps({"sentences": sents})
    if system.startswith("You are a strict fact-checker"):
        lines = re.findall(r"^(\d+)\. (.+)$", user, re.M)
        return json.dumps({"results": [{"i": int(i), "verdict": "unsupported" if "collapsed" in t else "supported", "evidence": "x"}
                                       for i, t in lines]})
    if system.startswith("Classify news events"):
        ids = re.findall(r"^\[(e\d+)\] (.+)$", user, re.M)
        return json.dumps({i: {"topic": "other", "type": "lowvalue" if "horoscope" in t.lower() else "news"} for i, t in ids})
    if system.startswith("You write the personal lines"):
        ids = re.findall(r"^\[(t\d+)\] (.+?) — ", user, re.M)
        return json.dumps({"briefing": ["Climate talks are in their final week with finance unresolved.",
                                        "Sensex closed at 79,876.12, down 0.55%.",
                                        "Sensex fell 3.2% overnight."],                                       # invented number
                           "why": {i: "This touches the cities and markets you follow." for i, _ in ids}})
    return "{}"


def reader_data(send_time: str = "00:00", with_other: bool = False) -> ReaderData:
    prefs = merge_prefs({"tz": "Asia/Dubai", "sendTime": send_time, "cities": ["London", "Singapore", "Toronto"],
                         "indices": ["^BSESN", "^NSEI", "^GSPC"], "watchlist": [], "forex": ["AED-INR", "USD-INR"],
                         "gold": {"currency": "AED", "unit": "g", "karats": ["24", "22"]},
                         "politics": ["india", "gulf", "world"], "topics": ["cricket"],
                         "sections": {"watchlist": False}})
    r = Reader(token="tok_muzaina_0001", email="muzaina@example.com", name="Muzaina", prefs=prefs, created_at="2026-11-20")
    readers = [r]
    if with_other:  # another reader's feeds widen the shared event pool (source of "outside your bubble")
        p2 = merge_prefs({"tz": "Asia/Kolkata", "cities": [], "politics": [], "topics": ["astronomy"],
                          "sections": {"politics": False, "cities": False, "tech": False}})
        readers.append(Reader(token="tok_other_0002", email="friend@example.com", name="Friend", prefs=p2, created_at="2026-11-22"))
    return ReaderData(readers=readers, feedback=[], clicks=[])

"""Google News RSS: feed keys, URL building and a dependency-free parser.

A *feed key* names one stream of headlines, shared by every reader who follows it:
  politics:<region>   city:<Name>|<CC>   tech:ai   tech:topic   topic:<text>|<CC>
"""
from __future__ import annotations

import hashlib
import html
import re
import time
import urllib.parse
from email.utils import parsedate_to_datetime

from .. import config
from ..models import Article


def _ed(cc: str | None) -> tuple[str, str, str]:
    return config.NEWS_EDITIONS.get((cc or "").upper(), config.NEWS_EDITIONS["US"])


def search_url(query: str, cc: str | None, window: str = "1d") -> str:
    hl, gl, ceid = _ed(cc)
    q = urllib.parse.quote(f"{query} when:{window}")
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={urllib.parse.quote(ceid)}"


def topic_url(topic: str, cc: str | None) -> str:
    hl, gl, ceid = _ed(cc)
    return f"https://news.google.com/rss/headlines/section/topic/{topic}?hl={hl}&gl={gl}&ceid={urllib.parse.quote(ceid)}"


def feed_urls(feed_key: str) -> tuple[str, str | None]:
    """Returns (primary url, wider-window fallback url or None)."""
    kind, _, rest = feed_key.partition(":")
    if kind == "politics":
        r = config.POLITICS[rest]
        if "topic" in r:
            return topic_url(r["topic"], r["ed"]), None
        return search_url(r["q"], r["ed"]), search_url(r["q"], r["ed"], "3d")
    if kind == "city":
        name, _, cc = rest.partition("|")
        q = f'"{name}"'
        return search_url(q, cc), search_url(q, cc, "3d")
    if kind == "tech":
        if rest == "ai":
            q = '"artificial intelligence" OR "AI model" OR "AI startup"'
            return search_url(q, "US"), None
        return topic_url("TECHNOLOGY", "US"), None
    if kind == "topic":
        text, _, cc = rest.partition("|")
        return search_url(text, cc), search_url(text, cc, "3d")
    raise ValueError(f"unknown feed key {feed_key}")


_ITEM = re.compile(r"<item>(.*?)</item>", re.S)


def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}(?:\s[^>]*)?>(.*?)</{name}>", block, re.S)
    if not m:
        return ""
    v = m.group(1)
    v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", v, flags=re.S)
    return html.unescape(v).strip()


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()[:80]


def article_id(url: str) -> str:
    return "a" + hashlib.sha1(url.encode()).hexdigest()[:12]


def parse_rss(xml: str, feed_key: str, now: float | None = None, max_age_h: float = 72) -> list[Article]:
    now = now or time.time()
    out: list[Article] = []
    for block in _ITEM.findall(xml):
        title = _tag(block, "title")
        link = _tag(block, "link")
        if not title or not link:
            continue
        src_m = re.search(r"<source[^>]*>(.*?)</source>", block, re.S)
        source = html.unescape(src_m.group(1)).strip() if src_m else ""
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)]
        desc = re.sub(r"\s+", " ", _strip_tags(_tag(block, "description"))).strip()
        # Google News descriptions often just repeat the title + outlet: drop those.
        if norm_title(desc).startswith(norm_title(title)) and len(desc) < len(title) + len(source) + 12:
            desc = ""
        try:
            pub = parsedate_to_datetime(_tag(block, "pubDate")).timestamp()
        except Exception:
            pub = now
        if now - pub > max_age_h * 3600:
            continue
        out.append(Article(id=article_id(link), url=link, title=title, source=source or "Unknown",
                           published=pub, description=desc[:400], feeds={feed_key}, first_seen=now))
    return out

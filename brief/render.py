"""Email rendering: classic newspaper, email-safe inline styles (same design as v1)."""
from __future__ import annotations

import html
import urllib.parse

from . import config

INK, PAPER, MUTED, RULE, OX, UP, DOWN = "#1b1b1b", "#f7f2e7", "#6b6256", "#cfc4ae", "#7d1d1d", "#1e6b3a", "#a11d1d"
SERIF = "Georgia, 'Times New Roman', Times, serif"


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def fmt_fixed(n: float, dp: int) -> str:
    return f"{n:,.{dp}f}"


def fmt_price(n: float) -> str:
    return fmt_fixed(n, 4 if abs(n) < 1 else 2)


def fmt_rate(n: float) -> str:
    return fmt_fixed(n, 2 if n >= 10 else 4 if n >= 0.1 else 6)


def fmt_pct(p) -> str:
    if p is None:
        return ""
    return ("+" if p > 0 else "-" if p < 0 else "") + f"{abs(p):.2f}%"


def market_text(numbers: dict) -> str:
    out = []
    q = lambda x: f"{x['label']}: {fmt_price(x['price'])}" + (f" ({fmt_pct(x['change_pct'])})" if x.get("change_pct") is not None else "")
    for k in ("markets", "watchlist"):
        for x in (numbers.get(k) or {}).get("items", []):
            out.append("- " + q(x))
    for r in (numbers.get("forex") or {}).get("rows", []):
        out.append(f"- 1 {r['from']} = {fmt_rate(r['rate'])} {r['to']}" + (f" ({fmt_pct(r['change_pct'])})" if r.get("change_pct") is not None else ""))
    g = numbers.get("gold")
    if g:
        for r in g["rows"]:
            out.append(f"- Gold {r['karat']}: {g['currency']} {fmt_price(r['price'])} {g['unit_label']}" + (f" ({fmt_pct(r['change_pct'])})" if r.get("change_pct") is not None else ""))
    return "\n".join(out)


class Links:
    def __init__(self, token: str, edition_id: str):
        self.token = token
        self.edition_id = edition_id
        self.app = config.APPS_SCRIPT_URL or ""

    def story(self, url: str, event_id: str, team: str, section: str) -> str:
        if not config.PAGES_URL or not self.app:
            return url
        frag = urllib.parse.urlencode({"u": url, "t": self.token, "s": event_id, "tm": team, "sec": section,
                                       "ed": self.edition_id, "cb": self.app})
        return f"{config.PAGES_URL}/go.html#{frag}"

    def vote(self, event_id: str, vote: str, section: str, title: str) -> str:
        q = urllib.parse.urlencode({"a": "fb", "t": self.token, "s": event_id, "v": vote, "sec": section, "ti": title[:140]})
        return f"{self.app}?{q}"

    def edit(self) -> str:
        return f"{self.app}?t={urllib.parse.quote(self.token)}"

    def unsub(self) -> str:
        return f"{self.app}?a=unsub&t={urllib.parse.quote(self.token)}"


def _chg(p) -> str:
    if p is None:
        return f'<span style="color:{MUTED}">—</span>'
    c = UP if p > 0 else DOWN if p < 0 else MUTED
    arrow = "▲ " if p > 0 else "▼ " if p < 0 else ""
    return f'<span style="color:{c};font-weight:bold">{arrow}{esc(fmt_pct(p))}</span>'


def _row(label, sub, value, pct) -> str:
    td = f"padding:7px 0;border-bottom:1px solid {RULE};font-family:{SERIF};"
    return (f'<tr><td style="{td}font-size:15px;color:{INK}">{esc(label)}'
            + (f' <span style="font-size:12px;color:{MUTED}">{esc(sub)}</span>' if sub else "")
            + f'</td><td align="right" style="{td}font-size:15px;color:{INK};white-space:nowrap">{esc(value)}</td>'
            f'<td align="right" width="96" style="{td}padding-left:12px;font-size:14px;white-space:nowrap">{_chg(pct)}</td></tr>')


def _sub(t) -> str:
    return (f'<tr><td colspan="3" style="padding:14px 0 2px 0;font-family:{SERIF};font-size:12px;letter-spacing:1.5px;'
            f'text-transform:uppercase;color:{OX};font-weight:bold">{esc(t)}</td></tr>')


def _head(title, sub="") -> str:
    return (f'<tr><td style="padding:26px 0 8px 0;border-bottom:1px solid {INK}">'
            f'<span style="font-family:{SERIF};font-size:13px;letter-spacing:2px;text-transform:uppercase;font-weight:bold;color:{INK}">{esc(title)}</span>'
            + (f'<span style="font-family:{SERIF};font-size:13px;color:{MUTED}">&nbsp;&nbsp;{sub}</span>' if sub else "") + "</td></tr>")


def _unavail(what) -> str:
    return f'<div style="padding:6px 0;font-family:{SERIF};font-size:14px;font-style:italic;color:{MUTED}">{esc(what)} is temporarily unavailable this morning.</div>'


def render(ed: dict) -> tuple[str, str]:
    """ed: the assembled edition dict from pipeline. Returns (html, text)."""
    L: Links = ed["links"]
    p = ed["prefs"]
    s = p.get("sections", {})
    nums = ed["numbers"]
    h = []

    def story_meta(item) -> str:
        e = item["event_id"]
        outlets = item["outlets"]
        others = item["others"]
        meta = esc(item["source"])
        if others:
            meta += " · also " + ", ".join(f'<a href="{esc(L.story(o["url"], e, item["team"], item["section"]))}" style="color:{MUTED}">{esc(o["source"])}</a>' for o in others[:3])
        if outlets > 1:
            meta += f" · {outlets} outlets"
        votes = (f'<a href="{esc(L.vote(e, "up", item["section"], item["headline"]))}" style="text-decoration:none" title="More like this">👍</a>&nbsp;&nbsp;'
                 f'<a href="{esc(L.vote(e, "down", item["section"], item["headline"]))}" style="text-decoration:none" title="Less like this">👎</a>')
        why = f'<div style="margin-top:3px;font-family:{SERIF};font-size:11.5px;font-style:italic;color:{MUTED}">{esc(item["explanation"])}</div>' if item.get("explanation") else ""
        return f'<div style="margin-top:6px;font-family:{SERIF};font-size:12px;color:{MUTED}">{meta}&nbsp;&nbsp;&nbsp;{votes}</div>{why}'

    def label(item) -> str:
        return (f'<span style="background:{OX};color:{PAPER};font-family:{SERIF};font-size:10px;letter-spacing:1px;'
                f'padding:1px 5px;margin-right:6px;text-transform:uppercase">Update</span>') if item.get("is_update") else ""

    # Masthead
    h.append(f'<tr><td><table width="100%" cellpadding="0" cellspacing="0" role="presentation"><tr>'
             f'<td style="font-family:{SERIF};font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:{MUTED}">Vol. II · No. {ed["edition_no"]}</td>'
             f'<td align="right" style="font-family:{SERIF};font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:{MUTED}">Edition for {esc(ed["name"])}</td></tr></table></td></tr>')
    h.append(f'<tr><td align="center" style="padding:8px 0 2px 0;font-family:{SERIF};font-size:56px;line-height:60px;font-weight:bold;letter-spacing:-1.5px;color:{INK}">The Brief</td></tr>')
    h.append(f'<tr><td align="center" style="padding:0 0 10px 0;font-family:{SERIF};font-size:14px;font-style:italic;color:{MUTED}">Your morning, on one page.</td></tr>')
    cities = " · ".join(esc(c) for c in ed["cities"])
    h.append(f'<tr><td align="center" style="border-top:3px double {INK};border-bottom:1px solid {INK};padding:7px 0">'
             f'<span style="font-family:{SERIF};font-size:12px;letter-spacing:1.5px;text-transform:uppercase;color:{INK}">{esc(ed["date_label"])}'
             + (f" &nbsp;·&nbsp; {cities}" if cities else "") + "</span></td></tr>")

    if ed["briefing"]:
        bullets = "".join(f'<div style="font-family:{SERIF};font-size:15px;line-height:22px;color:{INK};padding:3px 0">■&nbsp; {esc(b)}</div>' for b in ed["briefing"])
        h.append(f'<tr><td style="padding:22px 0 4px 0"><table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#efe7d6;border-left:3px solid {OX}"><tr><td style="padding:14px 18px">'
                 f'<div style="font-family:{SERIF};font-size:12px;letter-spacing:2px;text-transform:uppercase;font-weight:bold;color:{OX};padding-bottom:6px">Today in 30 seconds</div>{bullets}</td></tr></table></td></tr>')

    # The Numbers
    want = (s.get("markets") and p.get("indices")) or (s.get("watchlist") and p.get("watchlist")) or (s.get("forex") and p.get("forex")) or s.get("gold")
    if want:
        h.append(_head("The Numbers", "straight from the data, no AI"))
        t = ['<tr><td><table width="100%" cellpadding="0" cellspacing="0" role="presentation">']
        if s.get("markets") and p.get("indices"):
            t.append(_sub("Markets"))
            m = nums.get("markets")
            t += [_row(x["label"], "", fmt_price(x["price"]), x.get("change_pct")) for x in (m or {}).get("items", [])] or [f'<tr><td colspan="3">{_unavail("Market data")}</td></tr>']
        if s.get("watchlist") and p.get("watchlist"):
            t.append(_sub("Your watchlist"))
            m = nums.get("watchlist")
            t += [_row(x["label"], x["symbol"], (x["currency"] + " " if x.get("currency") else "") + fmt_price(x["price"]), x.get("change_pct")) for x in (m or {}).get("items", [])] or [f'<tr><td colspan="3">{_unavail("Watchlist data")}</td></tr>']
        if s.get("forex") and p.get("forex"):
            t.append(_sub("Currencies"))
            m = nums.get("forex")
            t += [_row(f"1 {r['from']} → {r['to']}", "", f"{fmt_rate(r['rate'])} {r['to']}", r.get("change_pct")) for r in (m or {}).get("rows", [])] or [f'<tr><td colspan="3">{_unavail("Currency data")}</td></tr>']
        if s.get("gold"):
            t.append(_sub("Gold"))
            g = nums.get("gold")
            t += [_row(f"Gold {r['karat']}", g["unit_label"], f"{g['currency']} {fmt_price(r['price'])}", r.get("change_pct")) for r in g["rows"]] if g else [f'<tr><td colspan="3">{_unavail("Gold data")}</td></tr>']
        t.append("</table></td></tr>")
        h.append("".join(t))
        if s.get("gold") and nums.get("gold"):
            h.append(f'<tr><td style="padding:6px 0 0 0;font-family:{SERIF};font-size:12px;font-style:italic;color:{MUTED}">Gold is spot-based; jewellers add making charges and local taxes. Currency and gold change is day-over-day.</td></tr>')

    # Top stories
    if ed["top"]:
        h.append(_head("Top Stories"))
        for i, it in enumerate(ed["top"]):
            border = f"border-bottom:1px solid {RULE};" if i < len(ed["top"]) - 1 else ""
            summ = f'<div style="font-family:{SERIF};font-size:16px;line-height:24px;color:{INK}">{esc(it["summary"])}</div>' if it["summary"] else ""
            why = (f'<div style="margin-top:8px;font-family:{SERIF};font-size:15px;line-height:22px;font-style:italic;color:{INK}">'
                   f'<span style="color:{OX};font-weight:bold;font-style:normal">Why it matters to you:</span> {esc(it["why"])}</div>') if it.get("why") else ""
            h.append(f'<tr><td style="padding:16px 0 14px 0;{border}">'
                     f'<div style="font-family:{SERIF};font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:{OX};font-weight:bold">{esc(it["section_title"])}</div>'
                     f'<div style="padding:4px 0 6px 0">{label(it)}<a href="{esc(L.story(it["url"], it["event_id"], it["team"], it["section"]))}" style="font-family:{SERIF};font-size:22px;line-height:28px;font-weight:bold;color:{INK};text-decoration:none">{esc(it["headline"])}</a></div>'
                     f'{summ}{why}{story_meta(it)}</td></tr>')

    # Sections
    for sec in ed["sections"]:
        sub = ""
        w = sec.get("weather")
        if w:
            sub = f'{w["icon"]} {esc(w["desc"])} · {round(w["hi"])}° / {round(w["lo"])}°C' + (f' · {w["rain"]}% rain' if w.get("rain") is not None else "")
        h.append(_head(sec["title"], sub))
        if not sec["items"]:
            h.append(f'<tr><td>{_unavail("News for this section")}</td></tr>')
            continue
        for i, it in enumerate(sec["items"]):
            border = f"border-bottom:1px solid {RULE};" if i < len(sec["items"]) - 1 else ""
            text = it["summary"] or it["headline"]
            h.append(f'<tr><td style="padding:10px 0;{border}">{label(it)}'
                     f'<a href="{esc(L.story(it["url"], it["event_id"], it["team"], it["section"]))}" style="font-family:{SERIF};font-size:16px;line-height:23px;color:{INK};text-decoration:none">{esc(text)}</a>'
                     f'{story_meta(it)}</td></tr>')

    # Outside your bubble
    if ed.get("bubble"):
        it = ed["bubble"]
        h.append(_head("Outside your bubble", "one story you didn't ask for"))
        text = it["summary"] or it["headline"]
        h.append(f'<tr><td style="padding:10px 0"><a href="{esc(L.story(it["url"], it["event_id"], it["team"], "bubble"))}" style="font-family:{SERIF};font-size:16px;line-height:23px;color:{INK};text-decoration:none">{esc(text)}</a>{story_meta(it)}</td></tr>')

    # Footer
    h.append(f'<tr><td style="padding:30px 0 0 0;border-bottom:3px double {INK}"></td></tr>')
    h.append(f'<tr><td align="center" style="padding:16px 0 4px 0;font-family:{SERIF};font-size:14px">'
             f'<a href="{esc(L.edit())}" style="color:{OX};font-weight:bold">Edit my interests</a><span style="color:{MUTED}">&nbsp;&nbsp;·&nbsp;&nbsp;</span>'
             f'<a href="{esc(L.unsub())}" style="color:{MUTED}">Unsubscribe</a></td></tr>')
    st = ed["stats"]
    how = (f"How this edition was made: {st['events_considered']} events from {st['articles_considered']} articles were ranked for you. "
           f"Summaries by Claude use only the linked sources; {st['claims_supported']} of {st['claims_total']} sentences passed fact-checking"
           f"{' and the rest were removed' if st['claims_total'] > st['claims_supported'] else ''}. ")
    h.append(f'<tr><td align="center" style="padding:8px 12px 0 12px;font-family:{SERIF};font-size:11px;line-height:16px;color:{MUTED}">'
             f'{esc(how)}Numbers: Yahoo Finance, currency-api, Open-Meteo. Stories via Google News, linked to their publishers. Not investment advice.</td></tr>')

    body = "".join(h)
    pre = esc(ed["briefing"][0] if ed["briefing"] else "Your morning, on one page.")
    html_out = (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>The Brief</title></head>'
                f'<body style="margin:0;padding:0;background:#e9e2d3"><div style="display:none;max-height:0;overflow:hidden">{pre}</div>'
                f'<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#e9e2d3"><tr><td align="center" style="padding:20px 10px">'
                f'<table width="640" cellpadding="0" cellspacing="0" role="presentation" style="width:100%;max-width:640px;background:{PAPER};border:1px solid #d6ccb8">'
                f'<tr><td style="padding:22px 28px 28px 28px"><table width="100%" cellpadding="0" cellspacing="0" role="presentation">{body}</table></td></tr></table></td></tr></table></body></html>')
    return html_out, render_text(ed)


def render_text(ed: dict) -> str:
    L = [f"THE BRIEF — {ed['date_label']}", f"Edition for {ed['name']}", ""]
    if ed["briefing"]:
        L += ["TODAY IN 30 SECONDS"] + [f"- {b}" for b in ed["briefing"]] + [""]
    mt = market_text(ed["numbers"])
    if mt:
        L += ["THE NUMBERS", mt, ""]
    if ed["top"]:
        L.append("TOP STORIES")
        for it in ed["top"]:
            L.append(f"* {it['headline']} ({it['source']})")
            if it["summary"]:
                L.append(f"  {it['summary']}")
            if it.get("why"):
                L.append(f"  Why it matters to you: {it['why']}")
            L.append(f"  {it['url']}")
        L.append("")
    for sec in ed["sections"]:
        L.append(sec["title"].upper())
        L += [f"- {it['summary'] or it['headline']} ({it['source']}) {it['url']}" for it in sec["items"]]
        L.append("")
    if ed.get("bubble"):
        b = ed["bubble"]
        L += ["OUTSIDE YOUR BUBBLE", f"- {b['summary'] or b['headline']} ({b['source']}) {b['url']}", ""]
    L += [f"Edit my interests: {ed['links'].edit()}", f"Unsubscribe: {ed['links'].unsub()}"]
    return "\n".join(L)

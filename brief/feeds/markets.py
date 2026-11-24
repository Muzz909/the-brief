"""The Numbers: markets, forex, gold and weather. Values go straight to the email, never through a model."""
from __future__ import annotations

import datetime as dt
import json
import math
import urllib.parse

from .. import config, http


def _json(url: str):
    r = http.get(url)
    if not r or r.status != 200:
        return None
    try:
        return json.loads(r.text)
    except ValueError:
        return None


# ---- Quotes (Yahoo Finance chart endpoint; swappable) -----------------------
def _yahoo(sym: str, host: str) -> str:
    return f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym, safe='')}?range=5d&interval=1d"


def _parse_yahoo(j, sym: str) -> dict | None:
    try:
        res = j["chart"]["result"][0]
        m = res.get("meta", {})
        ts = res.get("timestamp") or []
        closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        pts = [(t, c) for t, c in zip(ts, closes) if c is not None and math.isfinite(c)]
        price = m.get("regularMarketPrice") if m.get("regularMarketPrice") is not None else (pts[-1][1] if pts else None)
        if price is None:
            return None
        prev = None
        if len(pts) >= 2:
            same_day = pts[-1][0] // 86400 >= (m.get("regularMarketTime") or pts[-1][0]) // 86400
            prev = pts[-2][1] if same_day else pts[-1][1]
        elif m.get("chartPreviousClose"):
            prev = m["chartPreviousClose"]
        label = config.INDICES.get(sym) or (m.get("shortName") or m.get("longName") or sym).title()
        return {"symbol": sym, "label": label, "price": float(price),
                "change_pct": (price - prev) / prev * 100 if prev else None, "currency": m.get("currency", ""),
                "as_of": m.get("regularMarketTime")}
    except (KeyError, IndexError, TypeError):
        return None


def quotes(symbols: list[str]) -> dict:
    items, missing = [], []
    for sym in symbols:
        q = _parse_yahoo(_json(_yahoo(sym, "query1")), sym) or _parse_yahoo(_json(_yahoo(sym, "query2")), sym)
        (items.append(q) if q else missing.append(sym))
    return {"items": items, "missing": missing}


# ---- Forex + gold (fawazahmed0 currency-api, daily) --------------------------
def _currency(base: str, tag: str = "latest"):
    b = base.lower()
    for url in (f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{tag}/v1/currencies/{b}.json",
                f"https://{tag}.currency-api.pages.dev/v1/currencies/{b}.json"):
        j = _json(url)
        if j and b in j:
            return j
    return None


def _prev_day(date: str) -> str:
    return (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()


def forex(pairs: list[str]) -> dict:
    rows = []
    for base in dict.fromkeys(p.split("-")[0] for p in pairs):
        latest = _currency(base)
        if not latest:
            continue
        prev = _currency(base, _prev_day(latest["date"])) if latest.get("date") else None
        b = base.lower()
        for pair in [p for p in pairs if p.split("-")[0] == base]:
            to = pair.split("-")[1].lower()
            rate = latest[b].get(to)
            if rate is None:
                continue
            was = (prev or {}).get(b, {}).get(to)
            rows.append({"pair": pair, "from": base, "to": to.upper(), "rate": rate,
                         "change_pct": (rate - was) / was * 100 if was else None})
    rows.sort(key=lambda r: pairs.index(r["pair"]))
    return {"rows": rows}


def gold(g: dict) -> dict | None:
    latest = _currency("xau")
    if not latest:
        return None
    cur = g.get("currency", "AED").lower()
    per_oz = latest["xau"].get(cur)
    if per_oz is None:
        return None
    prev = _currency("xau", _prev_day(latest["date"])) if latest.get("date") else None
    prev_oz = (prev or {}).get("xau", {}).get(cur)
    unit_label, grams = config.GOLD_UNITS.get(g.get("unit", "g"), config.GOLD_UNITS["g"])
    pct = (per_oz - prev_oz) / prev_oz * 100 if prev_oz else None
    rows = [{"karat": f"{k}K", "price": per_oz / config.TROY_OZ_G * grams * int(k) / 24, "change_pct": pct}
            for k in g.get("karats", ["24"])]
    return {"currency": g.get("currency", "AED"), "unit_label": unit_label, "rows": rows, "date": latest.get("date")}


# ---- Places + weather (Open-Meteo) -------------------------------------------
WMO = [((0,), "Clear", "☀️"), ((1,), "Mostly clear", "🌤️"), ((2,), "Partly cloudy", "⛅"), ((3,), "Overcast", "☁️"),
       ((45, 48), "Fog", "🌫️"), ((51, 53, 55, 56, 57), "Drizzle", "🌦️"), ((61, 63, 65, 66, 67), "Rain", "🌧️"),
       ((71, 73, 75, 77, 85, 86), "Snow", "❄️"), ((80, 81, 82), "Showers", "🌦️"), ((95, 96, 99), "Thunderstorms", "⛈️")]


def geocode(city: str, store=None) -> dict | None:
    key = "geo:" + city.lower().strip()
    if store is not None:
        hit = store.get_meta(key)
        if hit:
            return hit
    j = _json("https://geocoding-api.open-meteo.com/v1/search?name=" + urllib.parse.quote(city) + "&count=1&language=en&format=json")
    g = ((j or {}).get("results") or [None])[0]
    if not g:
        return None
    out = {"name": g["name"], "cc": (g.get("country_code") or "").upper(), "lat": g["latitude"], "lon": g["longitude"]}
    if store is not None:
        store.set_meta(key, out)
    return out


def weather(geo: dict) -> dict | None:
    j = _json(f"https://api.open-meteo.com/v1/forecast?latitude={geo['lat']}&longitude={geo['lon']}"
              "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code&timezone=auto&forecast_days=1")
    try:
        d = j["daily"]
        code = d["weather_code"][0]
        desc, icon = next(((t, i) for codes, t, i in WMO if code in codes), ("Weather", "🌡️"))
        rain = (d.get("precipitation_probability_max") or [None])[0]
        return {"hi": d["temperature_2m_max"][0], "lo": d["temperature_2m_min"][0], "rain": rain, "desc": desc, "icon": icon}
    except (TypeError, KeyError, IndexError):
        return None

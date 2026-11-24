"""Interleaving readout: credit clicks and 👍 to the ranker that contributed each story."""
from __future__ import annotations

import math
from collections import defaultdict

from .readers import ReaderData
from .store import Store


def sign_test_p(wins: int, losses: int) -> float:
    """Two-sided exact binomial sign test (ties dropped)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def credit(store: Store, data: ReaderData) -> dict:
    imps = [r for r in store.all_impressions() if r["team"] in ("v2", "base")]
    team_of = {(r["reader"], r["event_id"]): (r["team"], r["edition_id"]) for r in imps}
    days: dict[str, dict[str, int]] = defaultdict(lambda: {"v2": 0, "base": 0})
    for e in data.clicks + [f for f in data.feedback if f.get("vote") == "up"]:
        key = (e.get("token"), e.get("storyKey"))
        if key in team_of:
            team, edition = team_of[key]
            days[edition][team] += 1
    editions = {r["edition_id"] for r in imps}
    wins = sum(1 for d in days.values() if d["v2"] > d["base"])
    losses = sum(1 for d in days.values() if d["base"] > d["v2"])
    ties = len(editions) - wins - losses
    return {"reader_days": len(editions), "v2_wins": wins, "base_wins": losses, "ties": ties,
            "p_value": round(sign_test_p(wins, losses), 4),
            "v2_engagements": sum(d["v2"] for d in days.values()),
            "base_engagements": sum(d["base"] for d in days.values())}


def readout(store: Store, data: ReaderData) -> str:
    c = credit(store, data)
    decided = c["v2_wins"] + c["base_wins"]
    rate = f"{c['v2_wins'] / decided:.0%}" if decided else "n/a"
    return (f"Interleaving readout\n"
            f"  reader-days: {c['reader_days']}  (decided: {decided}, ties: {c['ties']})\n"
            f"  v2 wins: {c['v2_wins']}  baseline wins: {c['base_wins']}  v2 win rate: {rate}\n"
            f"  engagements credited  v2: {c['v2_engagements']}  baseline: {c['base_engagements']}\n"
            f"  sign test p = {c['p_value']}  (hypothesis: v2 wins > 50% of decided reader-days)")

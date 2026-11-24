"""Thin LLM layer: Claude in production, a scripted fake in tests. Tracks token usage per model."""
from __future__ import annotations

import json
import re
from typing import Callable

from . import config


def parse_json(text: str):
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s, e = text.find(open_c), text.rfind(close_c)
        if s >= 0 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except ValueError:
                continue
    return None


class LLM:
    def __init__(self):
        self.usage: dict[str, dict[str, int]] = {}

    def _track(self, model: str, inp: int, out: int) -> None:
        u = self.usage.setdefault(model, {"input": 0, "output": 0, "calls": 0})
        u["input"] += inp
        u["output"] += out
        u["calls"] += 1

    @property
    def totals(self) -> dict[str, int]:
        return {"input": sum(u["input"] for u in self.usage.values()),
                "output": sum(u["output"] for u in self.usage.values()),
                "calls": sum(u["calls"] for u in self.usage.values())}

    def cost_usd(self) -> float | None:
        total, priced = 0.0, False
        for m, u in self.usage.items():
            pin, pout = config.PRICE_PER_MTOK.get(m, (0, 0))
            if pin or pout:
                priced = True
                total += u["input"] / 1e6 * pin + u["output"] / 1e6 * pout
        return round(total, 5) if priced else None

    def complete(self, model: str, system: str, user: str, max_tokens: int = 1500) -> str:
        raise NotImplementedError

    def json(self, model: str, system: str, user: str, max_tokens: int = 1500):
        out = parse_json(self.complete(model, system, user, max_tokens))
        if out is None:  # one retry with a nudge
            out = parse_json(self.complete(model, system, user + "\n\nReturn ONLY valid JSON.", max_tokens))
        return out


class ClaudeLLM(LLM):
    def __init__(self, api_key: str | None = None):
        super().__init__()
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key or config.ANTHROPIC_API_KEY, max_retries=3, timeout=90)

    def complete(self, model: str, system: str, user: str, max_tokens: int = 1500) -> str:
        kwargs = dict(
            model=model, max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        try:
            msg = self.client.messages.create(temperature=0.2, **kwargs)
        except Exception as e:  # some models don't accept sampling params: retry without
            if "temperature" not in str(e).lower():
                raise
            msg = self.client.messages.create(**kwargs)
        self._track(model, msg.usage.input_tokens, msg.usage.output_tokens)
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


class FakeLLM(LLM):
    """Scripted model for tests/evals without a key: handler(model, system, user) -> text."""

    def __init__(self, handler: Callable[[str, str, str], str]):
        super().__init__()
        self.handler = handler
        self.calls: list[tuple[str, str, str]] = []

    def complete(self, model: str, system: str, user: str, max_tokens: int = 1500) -> str:
        self.calls.append((model, system, user))
        out = self.handler(model, system, user)
        self._track(model, len(system + user) // 4, len(out) // 4)
        return out

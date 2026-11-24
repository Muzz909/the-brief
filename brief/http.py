"""HTTP helpers: one place for timeouts, retries, parallel fetches and an in-run cache.

Tests swap `TRANSPORT` for a fake, so nothing touches the network.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import requests

from . import config


@dataclass
class Response:
    status: int
    text: str


def _requests_transport(url: str, timeout: float) -> Response:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": config.USER_AGENT}, allow_redirects=True)
    return Response(r.status_code, r.text)


TRANSPORT: Callable[[str, float], Response] = _requests_transport
_CACHE: dict[str, Response] = {}


def get(url: str, retries: int = 1, use_cache: bool = True) -> Response | None:
    if use_cache and url in _CACHE:
        return _CACHE[url]
    last = None
    for attempt in range(retries + 1):
        try:
            r = TRANSPORT(url, config.HTTP_TIMEOUT)
            if r.status == 200:
                _CACHE[url] = r
                return r
            last = r
            if r.status not in (429, 500, 502, 503, 504):
                break
        except Exception:  # network error: retry once
            last = None
        if attempt < retries:
            time.sleep(1.5)
    return last


def get_many(urls: list[str], workers: int = 8) -> list[Response | None]:
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(get, urls))


def clear_cache() -> None:
    _CACHE.clear()

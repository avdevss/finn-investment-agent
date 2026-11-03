# Shared HTTP layer for every scraped source: the BSE and screener header sets,
# a polite per-host throttle, and retry with growing waits on 429 and 5xx.

from __future__ import annotations

import logging
import random
import time
from typing import Final

import requests

log = logging.getLogger(__name__)

# Akamai fronts api.bseindia.com and 403s a stale User-Agent. A current Chrome
# string plus the same-site fetch headers is what gets through.
CHROME_UA: Final = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

BSE_API_HEADERS: Final[dict[str, str]] = {
    "User-Agent": CHROME_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Origin": "https://www.bseindia.com/",
    "Referer": "https://www.bseindia.com/",
    "Sec-Fetch-Site": "same-site",
}

BSE_PDF_HEADERS: Final[dict[str, str]] = {
    "User-Agent": CHROME_UA,
    "Accept": "application/pdf,*/*",
    "Referer": "https://www.bseindia.com/",
}

SCREENER_HEADERS: Final[dict[str, str]] = {
    "User-Agent": CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BSE_ANN_URL: Final = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_PDF_BASE: Final = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
SCREENER_COMPANY_URL: Final = "https://www.screener.in/company/{symbol}/consolidated/"

RETRY_STATUS: Final = frozenset({408, 425, 429, 500, 502, 503, 504, 509})

# screener.in asks for 1.5s between hits; BSE tolerates more but not much.
_MIN_INTERVAL: Final[dict[str, float]] = {
    "www.screener.in": 1.5,
    "api.bseindia.com": 0.4,
    "www.bseindia.com": 0.4,
}
_DEFAULT_INTERVAL: Final = 0.5
_last_hit: dict[str, float] = {}

_session = requests.Session()


def _throttle(host: str) -> None:
    interval = _MIN_INTERVAL.get(host, _DEFAULT_INTERVAL)
    wait = interval - (time.monotonic() - _last_hit.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.monotonic()


def _retry_after(resp: requests.Response, attempt: int, base: float) -> float:
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), 120.0)
        except ValueError:
            pass
    return base * (2 ** attempt) + random.uniform(0, 0.5)


def get(url: str, *, headers: dict[str, str], params: dict[str, object] | None = None,
        attempts: int = 5, timeout: float = 30.0, backoff: float = 1.5,
        stream: bool = False) -> requests.Response:
    """GET with throttling and growing waits on 429/5xx. Raises on final failure."""
    host = requests.utils.urlparse(url).netloc
    last: Exception | None = None

    for attempt in range(attempts):
        _throttle(host)
        try:
            resp = _session.get(url, headers=headers, params=params,
                                timeout=timeout, stream=stream)
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            wait = backoff * (2 ** attempt) + random.uniform(0, 0.5)
            log.warning("%s on %s, attempt %d/%d, sleeping %.1fs",
                        type(e).__name__, host, attempt + 1, attempts, wait)
            time.sleep(wait)
            continue

        if resp.status_code in RETRY_STATUS and attempt < attempts - 1:
            wait = _retry_after(resp, attempt, backoff)
            log.warning("HTTP %d on %s, attempt %d/%d, sleeping %.1fs",
                        resp.status_code, host, attempt + 1, attempts, wait)
            resp.close()
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp

    raise requests.RequestException(f"{url} failed after {attempts} attempts") from last


def get_json(url: str, *, headers: dict[str, str],
             params: dict[str, object] | None = None, **kw: object) -> dict | list | str:
    return get(url, headers=headers, params=params, **kw).json()  # type: ignore[arg-type]

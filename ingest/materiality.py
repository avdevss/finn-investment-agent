# Materiality scoring: transparent arithmetic, no model, 0 to 100. The breakdown
# is stored alongside the score and shown in the UI when the score is tapped,
# because an investor trusts a number they can take apart.

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Final

import psycopg

from ingest.trading_days import Calendar

CARD_THRESHOLD: Final = 60      # full card at the top of the digest
ROW_THRESHOLD: Final = 30       # compact row; below this collapses into one grey line

CATEGORY_WEIGHT: Final[dict[str, int]] = {
    "results": 55, "M&A": 55, "management_change": 55, "fund_raise": 55, "litigation": 55,
    "capex": 35, "credit_rating": 35, "dividend": 35, "bonus_split": 35, "pledging": 35,
    "earnings_call": 25, "insider_trading": 25, "press_release": 25,
    "agm_egm": 12, "postal_ballot": 12, "investor_meetings": 12,
    "unclassified": 3,
}

PRICE_CAP: Final = 25.0
VOLUME_CAP: Final = 15.0

# A notice of results is a calendar entry, not the numbers: it says a board will meet
# on a date to consider the quarter. It stays in the results category, because that is
# what it is about, but it cannot carry the weight of the results themselves.
NOTICE_CAP: Final = 25
_NOTICE: Final = re.compile(r"\bintimation\b|to consider|to announce", re.I)

RESULTS: Final = "results"


def is_notice(title: str) -> bool:
    return bool(_NOTICE.search(title or ""))


def notice_capped(category: str, title: str, extracted: Any) -> bool:
    """True for a results notice the filing itself confirmed carries no statement.

    Both halves are required. The title alone is not enough, because a real results
    filing is often titled as the outcome of the meeting that was intimated; and the
    missing statement alone is not enough, because a parser failure on a genuine
    statement should not quietly demote it. `extracted` is None until the PDF has
    been read, and nothing is capped before then.
    """
    if category != RESULTS or extracted is None or not is_notice(title):
        return False
    return not extracted.get("metrics")


def score(category: str, pct_change: float | None, vol_ratio: float | None,
          price_date: Any = None, price_basis: str = "",
          notice: bool = False) -> tuple[int, dict[str, Any]]:
    """Returns the 0 to 100 score and the breakdown to store in score_parts."""
    uncapped = CATEGORY_WEIGHT.get(category, CATEGORY_WEIGHT["unclassified"])
    category_weight = min(uncapped, NOTICE_CAP) if notice else uncapped

    price_component = (0.0 if pct_change is None
                       else min(PRICE_CAP, abs(float(pct_change)) * 4))
    volume_component = (0.0 if vol_ratio is None
                        else min(VOLUME_CAP, max(0.0, (float(vol_ratio) - 1) * 10)))

    total = min(100, round(category_weight + price_component + volume_component))
    parts: dict[str, Any] = {
        "category": category,
        "category_weight": category_weight,
        "price_component": round(price_component, 1),
        "volume_component": round(volume_component, 1),
        "pct_change": None if pct_change is None else round(float(pct_change), 2),
        "vol_ratio": None if vol_ratio is None else round(float(vol_ratio), 2),
        "price_date": price_date.isoformat() if price_date else None,
        "price_basis": price_basis,
        "total": total,
    }
    if category_weight != uncapped:
        parts["notice_cap"] = {
            "uncapped": uncapped,
            "reason": "notice of a results meeting, no statement in the filing",
        }
    return total, parts


class Scorer:
    """Scores an announcement against the price move its filing could have caused.

    Holds the trading calendar and the price table in memory so that the loaders and
    the re-classification pass resolve a filing timestamp to a trading day the same
    way, from one copy of the logic.
    """

    def __init__(self, calendar: Calendar,
                 prices: dict[tuple[str, date], tuple[Any, Any]]):
        self._calendar = calendar
        self._prices = prices

    @classmethod
    def load(cls, conn: psycopg.Connection) -> Scorer:
        with conn.cursor() as cur:
            cur.execute("select symbol, date, pct_change, vol_ratio from prices")
            prices = {(s, d): (p, v) for s, d, p, v in cur.fetchall()}
        return cls(Calendar.load(conn), prices)

    def apply(self, symbol: str, published_at: datetime, category: str,
              notice: bool = False) -> tuple[int, dict[str, Any]]:
        day = self._calendar.resolve(symbol, published_at)
        pct_change, vol_ratio = (self._prices.get((symbol, day.date), (None, None))
                                 if day.date else (None, None))

        missing = day.date is not None and (symbol, day.date) not in self._prices
        basis = f"unavailable: no price row for {day.date}" if missing else day.basis
        return score(category, pct_change, vol_ratio,
                     None if missing else day.date, basis, notice)

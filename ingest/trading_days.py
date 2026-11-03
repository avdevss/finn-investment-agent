# Maps a filing timestamp to the trading day whose price move it could have caused.
# A filing published at 23:05 cannot have moved that day's close, so scoring it
# against that day's pct_change would be wrong.
#
# The calendar is the set of dates actually present in prices for the symbol, which
# is the exchange calendar as observed. Weekends, market holidays and trading halts
# all fall out of that with no holiday list to maintain.

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Final
from zoneinfo import ZoneInfo

import psycopg

IST: Final = ZoneInfo("Asia/Kolkata")
MARKET_CLOSE: Final = time(15, 30)

SAME_DAY: Final = "same_day"
NEXT_TRADING_DAY: Final = "next_trading_day"


@dataclass(frozen=True)
class TradingDay:
    date: date | None
    basis: str          # same_day | next_trading_day | unavailable: <reason>


class Calendar:
    """Per symbol trading dates, loaded once from the prices table."""

    def __init__(self, days_by_symbol: dict[str, list[date]]):
        self._days = days_by_symbol

    @classmethod
    def load(cls, conn: psycopg.Connection) -> Calendar:
        with conn.cursor() as cur:
            cur.execute("select symbol, date from prices order by symbol, date")
            days: dict[str, list[date]] = {}
            for symbol, day in cur.fetchall():
                days.setdefault(symbol, []).append(day)
        return cls(days)

    def resolve(self, symbol: str, published_at: datetime) -> TradingDay:
        days = self._days.get(symbol)
        if not days:
            return TradingDay(None, "unavailable: no price rows for symbol")

        local = published_at.astimezone(IST)
        filed_on = local.date()
        index = bisect.bisect_left(days, filed_on)
        on_trading_day = index < len(days) and days[index] == filed_on

        if on_trading_day and local.time() <= MARKET_CLOSE:
            return TradingDay(filed_on, SAME_DAY)

        # After the close, or not a trading day at all: the next session.
        nxt = index + 1 if on_trading_day else index
        if nxt >= len(days):
            return TradingDay(None, f"unavailable: no trading day on or after {filed_on}")
        return TradingDay(days[nxt], NEXT_TRADING_DAY)

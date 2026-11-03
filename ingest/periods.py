# Fiscal period labels. The Indian financial year runs April to March, so a quarter
# ending September 2026 is Q2 of FY27. Every period string in the project is made
# here, so a promise's target period and an actual's period cannot drift apart.
#
# Month parsing is deliberately tolerant: filing PDFs come back with OCR damage
# ("30th Seo'25" for Sep), and a misread month would silently mislabel a column.

from __future__ import annotations

import re
from datetime import date
from typing import Final

QUARTER_END_MONTHS: Final = (3, 6, 9, 12)
_QUARTER_OF_MONTH: Final[dict[int, int]] = {3: 4, 6: 1, 9: 2, 12: 3}
_MONTHS: Final = ("jan", "feb", "mar", "apr", "may", "jun",
                  "jul", "aug", "sep", "oct", "nov", "dec")
_LAST_DAY: Final[dict[int, int]] = {3: 31, 6: 30, 9: 30, 12: 31}

_DATE_PATTERNS: Final = (
    # 30th Sep'26 | 30 September 2026 | 30-sep-26 | 31st Mar'26
    re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?[\s\-/.']*([a-z]{3,9})[\s\-/.']*'?(\d{2,4})", re.I),
    # Sep 2026, the screener quarterly header
    re.compile(r"([a-z]{3,9})\s+(\d{4})", re.I),
)


def fiscal_year(d: date) -> int:
    """The FY a date belongs to, as a full year: 30 Sep 2026 -> 2027."""
    return d.year + 1 if d.month >= 4 else d.year


def label(period_end: date) -> str:
    """Q2FY27 for a quarter ending 30 Sep 2026."""
    quarter = _QUARTER_OF_MONTH.get(period_end.month)
    if quarter is None:
        raise ValueError(f"{period_end} is not a quarter end")
    return f"Q{quarter}FY{fiscal_year(period_end) % 100:02d}"


def fy_label(period_end: date) -> str:
    """FY27 for a year ending 31 Mar 2027."""
    return f"FY{fiscal_year(period_end) % 100:02d}"


def month_number(token: str) -> int | None:
    """Month from a name, tolerating one wrong character from a bad PDF extraction."""
    head = re.sub(r"[^a-z]", "", token.lower())[:3]
    if len(head) < 3:
        return None
    if head in _MONTHS:
        return _MONTHS.index(head) + 1

    near = [i for i, m in enumerate(_MONTHS)
            if sum(a != b for a, b in zip(head, m)) == 1]
    return near[0] + 1 if len(near) == 1 else None


def parse_date(text: str) -> date | None:
    """A day/month/year or month/year date out of messy document text."""
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text)
        if m is None:
            continue
        groups = m.groups()
        day, month_token, year_token = (groups if len(groups) == 3
                                        else ("", groups[0], groups[1]))
        month = month_number(month_token)
        if month is None:
            continue

        year = int(year_token)
        if year < 100:
            year += 2000
        if not 2000 <= year <= 2100:
            continue
        # No day means a month column: take the month end.
        return date(year, month, int(day) if day else _month_end(year, month))
    return None


def _month_end(year: int, month: int) -> int:
    if month == 2:
        return 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
    return 30 if month in (4, 6, 9, 11) else 31


def is_quarter_end(d: date) -> bool:
    return d.month in QUARTER_END_MONTHS and d.day >= 28


def quarter_end(year: int, month: int) -> date:
    """The quarter end for whatever quarter a month falls in."""
    end_month = min(m for m in QUARTER_END_MONTHS if m >= month)
    return date(year, end_month, _LAST_DAY[end_month])


def previous_quarter_end(d: date) -> date:
    month, year = d.month - 3, d.year
    if month < 1:
        month, year = month + 12, year - 1
    return date(year, month, _LAST_DAY[month])


def next_quarter_end(d: date) -> date:
    month, year = d.month + 3, d.year
    if month > 12:
        month, year = month - 12, year + 1
    return date(year, month, _LAST_DAY[month])


def year_ago(d: date) -> date:
    """The same period end one year earlier. A March year end is also a quarter end,
    so this serves the quarterly and the annual actuals alike."""
    return date(d.year - 1, d.month, _LAST_DAY[d.month])


def reported_quarter_end(year: int, month: int) -> date:
    """The quarter a call held in this month reports on.

    A concall in Nov 2026 discusses the quarter that ended Sep 2026, so it is the
    last quarter end strictly before the month of the call.
    """
    earlier = [m for m in QUARTER_END_MONTHS if m < month]
    if not earlier:
        return date(year - 1, 12, 31)
    end_month = max(earlier)
    return date(year, end_month, _LAST_DAY[end_month])


# What a label spans. FY26 and Q4FY26 both end on 31 Mar 2026, so the end date on
# its own cannot say which set of actuals a target belongs against; the kind can.
QUARTER: Final = "quarter"
YEAR: Final = "year"
HALF: Final = "half"


def parse_period(text: str) -> tuple[date, str] | None:
    """(period end, kind) for a label a model produced: Q2FY27, FY26, H1FY27, Q4 FY2026."""
    cleaned = re.sub(r"[^A-Z0-9]", "", (text or "").upper())

    m = re.match(r"^Q([1-4])FY(\d{2,4})$", cleaned)
    if m:
        quarter, fy = int(m.group(1)), _full_year(m.group(2))
        end_month = (6, 9, 12, 3)[quarter - 1]
        return date(fy if end_month == 3 else fy - 1, end_month,
                    _LAST_DAY[end_month]), QUARTER

    m = re.match(r"^FY(\d{2,4})$", cleaned)
    if m:
        return date(_full_year(m.group(1)), 3, 31), YEAR

    m = re.match(r"^H([12])FY(\d{2,4})$", cleaned)
    if m:
        fy = _full_year(m.group(2))
        return (date(fy - 1, 9, 30) if m.group(1) == "1" else date(fy, 3, 31)), HALF
    return None


def normalise_label(text: str) -> str:
    """FY2030 -> FY30, 'Q4 FY 2026' -> Q4FY26. Anything unrecognised comes back trimmed."""
    cleaned = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
    m = re.match(r"^((?:Q[1-4]|H[12])?)FY(\d{2}|\d{4})$", cleaned)
    if m is None:
        return (text or "").strip()
    return f"{m.group(1)}FY{int(m.group(2)) % 100:02d}"


def parse_label(text: str) -> date | None:
    """Period end alone, for callers that do not care what the label spans."""
    got = parse_period(text)
    return got[0] if got else None


def _full_year(token: str) -> int:
    value = int(token)
    return value if value > 100 else 2000 + value

# Pulls the headline P&L numbers out of a results filing PDF.
#
# pdfplumber's extract_tables() is unusable on these documents: it merges six
# numeric columns into one cell and garbles parentheses. The text lines are clean
# and column aligned, so they are parsed directly and a model is called only when
# this fails.
#
# The numbers are scored against real OCR damage: a label can arrive with its
# spaces missing ("Revenuefrom operations"), a decimal point can come back as a
# comma ("16,66"), and a month can lose a letter ("30th Seo'25").

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from ingest import periods

log = logging.getLogger(__name__)

# Enumerators the statutory format puts in front of a line label: "1 ", "a) ",
# "(b) ", "d) ". Removed before the label is normalised, or "d) Finance costs"
# normalises to "dfinancecosts" and matches nothing.
_ENUMERATOR: Final = re.compile(r"^[\s(\[]*(?:\d{1,2}|[a-z]|[ivx]{1,4})[\s.)\]]+", re.I)

# A numeric token, allowing the parenthesised negatives the format uses and the
# "{" that OCR produces in place of "(".
_NUM: Final = r"[({\[]?-?\d[\d,.]*[)}\]]?"
_NUMBERS_TAIL: Final = re.compile(rf"^(?P<label>.*?)\s+(?P<nums>(?:{_NUM}\s+)*{_NUM})\s*$")
_ALL_NUMBERS: Final = re.compile(rf"^(?:{_NUM}\s+)*{_NUM}$")

# Label prefixes per metric, most specific first. A normalised label matches when it
# equals a prefix or starts with it, and the earliest prefix in the tuple wins, so
# "Profit After Tax and Share of Profit of Associates" beats the bare
# "Profit After Tax" line that sits above it in a consolidated statement.
LABELS: Final[dict[str, tuple[str, ...]]] = {
    "revenue": ("revenuefromoperations", "incomefromoperations",
                "netsalesincomefromoperations", "totalincomefromoperations"),
    "other_income": ("otherincome",),
    "total_income": ("totalincome",),
    "finance_costs": ("financecost", "interestexpended", "interest"),
    "depreciation": ("depreciation",),
    "pbt": ("profitbeforetax", "profitlossbeforetax", "profitbeforetaxation"),
    "pat": ("profitaftertaxandshareofprofit", "profitlossaftertaxandshareofprofit",
            "netprofitfortheperiod", "profitaftertax", "profitlossaftertax",
            "profitfortheperiod", "netprofitlossfortheperiod"),
}

EBITDA_BASIS: Final = "PBT + finance costs + depreciation - other income"
FINANCIALS_BASIS: Final = "not applicable for financials"

_CONSOLIDATED: Final = re.compile(r"\bconsolidated\b", re.I)
_STANDALONE: Final = re.compile(r"\bstandalone\b", re.I)
_PNL_HINT: Final = re.compile(r"revenue\s*from\s*operation|income\s*from\s*operation|"
                              r"total\s*income|profit\s*before\s*tax", re.I)
_UNITS: Final = re.compile(r"\bin\s+(crore|lakh|million|billion)s?\b", re.I)

COLUMNS: Final = ("current", "prior_quarter", "year_ago")


class ParseError(RuntimeError):
    """The statement could not be read deterministically; the model should try."""


@dataclass(frozen=True)
class Row:
    label: str
    key: str
    numbers: list[float | None]


def normalise_label(raw: str) -> str:
    """Lowercase letters only, with any leading enumerator removed first."""
    return re.sub(r"[^a-z]", "", _ENUMERATOR.sub("", raw).lower())


def parse_number(token: str) -> float | None:
    """A figure from the statement, tolerating OCR damage.

    Parentheses and the "{" OCR substitutes for them mean negative. A final comma
    group of exactly two digits is a misread decimal point: Indian digit grouping
    always ends in a group of three, so "16,66" can only be 16.66.
    """
    body = token.strip()
    negative = body[:1] in "({[" or body[-1:] in ")}]"
    body = re.sub(r"[^\d.,]", "", body)
    if not body or not any(ch.isdigit() for ch in body):
        return None

    if "." not in body:
        tail = body.rsplit(",", 1)
        if len(tail) == 2 and len(tail[1]) == 2:
            body = f"{tail[0]}.{tail[1]}"
    body = body.replace(",", "")

    try:
        value = float(body)
    except ValueError:
        return None
    return -value if negative else value


def _rows(lines: list[str]) -> list[Row]:
    """Label plus trailing figures per line.

    A label whose figures sit on the following line is joined to it: the format
    wraps a long label around its own numbers, as in "Profit After Tax and Share
    of Profit/ (Loss) of" / "29,550 23,196 ..." / "Associates and Joint Ventures".
    """
    out: list[Row] = []
    pending: str | None = None

    for line in lines:
        text = " ".join(line.split())
        if not text:
            continue

        if _ALL_NUMBERS.match(text):
            if pending:
                out.append(Row(pending, normalise_label(pending),
                               [parse_number(t) for t in text.split()]))
                pending = None
            continue

        m = _NUMBERS_TAIL.match(text)
        if m is None:
            pending = text
            continue

        label = m.group("label").strip()
        out.append(Row(label, normalise_label(label),
                       [parse_number(t) for t in m.group("nums").split()]))
        pending = None
    return out


def _match(rows: list[Row], metric: str) -> Row | None:
    """The best row for a metric: earliest label prefix wins, then first occurrence."""
    best: tuple[int, int, Row] | None = None
    for position, row in enumerate(rows):
        if len([n for n in row.numbers if n is not None]) < 3:
            continue
        for rank, prefix in enumerate(LABELS[metric]):
            if row.key == prefix or row.key.startswith(prefix):
                if best is None or (rank, position) < (best[0], best[1]):
                    best = (rank, position, row)
                break
    return None if best is None else best[2]


def _column_dates(lines: list[str]) -> list[date]:
    """The period ends on the header row, as the document orders them."""
    best: list[date] = []
    for line in lines:
        tokens = re.findall(r"\d{1,2}\s*(?:st|nd|rd|th)?[\s\-/.']*[A-Za-z]{3,9}[\s\-/.']*'?\d{2,4}",
                            line)
        found = [d for d in (periods.parse_date(t) for t in tokens) if d is not None]
        if len(found) > len(best):
            best = found
    return best


def _quarter_columns(dates: list[date]) -> dict[str, int]:
    """Map current / prior_quarter / year_ago onto column indices.

    The first three columns are the quarter group in both layouts measured, but that
    is verified rather than assumed: the triplet must be a quarter end followed by
    the previous quarter and the same quarter a year earlier. A header that does not
    hold that shape is handed to the model instead of guessed at.
    """
    if len(dates) < 3:
        raise ParseError(f"header had {len(dates)} dates, need at least 3")

    current, prior, year_ago = dates[0], dates[1], dates[2]
    if not periods.is_quarter_end(current):
        raise ParseError(f"first column {current} is not a quarter end")
    if prior != periods.previous_quarter_end(current):
        raise ParseError(f"second column {prior} is not the quarter before {current}")
    if year_ago != periods.year_ago(current):
        raise ParseError(f"third column {year_ago} is not a year before {current}")
    return {"current": 0, "prior_quarter": 1, "year_ago": 2}


def pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100, 1)


def _figures(row: Row | None, cols: dict[str, int]) -> dict[str, Any] | None:
    if row is None:
        return None
    values = {name: (row.numbers[i] if i < len(row.numbers) else None)
              for name, i in cols.items()}
    if values["current"] is None:
        return None
    return {**values, "label": row.label,
            "qoq_pct": pct(values["current"], values["prior_quarter"]),
            "yoy_pct": pct(values["current"], values["year_ago"])}


def _score_page(text: str) -> int:
    """How much this page looks like the statement we want. 0 means not a candidate."""
    if not _PNL_HINT.search(text):
        return 0
    lines = text.splitlines()
    if len(_column_dates(lines)) < 3:
        return 0
    return 2 if _CONSOLIDATED.search(text) else 1


def pick_page(pages: list[str]) -> tuple[int, str, str]:
    """(index, text, basis) of the best statement page. Consolidated beats standalone."""
    scored = [(_score_page(t), -i, i) for i, t in enumerate(pages)]
    best = max(scored)
    if best[0] == 0:
        raise ParseError("no page carries a P&L statement with a quarter column header")
    index = best[2]
    text = pages[index]
    basis = ("consolidated" if _CONSOLIDATED.search(text)
             else "standalone" if _STANDALONE.search(text) else "unstated")
    return index, text, basis


def extract(pages: list[str], *, is_financial: bool) -> dict[str, Any]:
    """The headline numbers for the quarter, the quarter before and the year before.

    Raises ParseError when the statement cannot be read, which is the signal to fall
    back to a model rather than publish a guess.
    """
    index, text, basis = pick_page(pages)
    lines = text.splitlines()
    cols = _quarter_columns(_column_dates(lines))
    rows = _rows(lines)

    found = {metric: _figures(_match(rows, metric), cols) for metric in LABELS}

    units = _UNITS.search(text)
    period_end = _column_dates(lines)[0]

    revenue = found["revenue"]
    if is_financial and revenue is None:
        # A lender reports total income where an industrial reports revenue.
        revenue = found["total_income"]

    metrics: dict[str, Any] = {"revenue": revenue, "pbt": found["pbt"], "pat": found["pat"]}
    metrics["ebitda"] = (None if is_financial
                         else _ebitda(found, cols))
    ebitda_basis = FINANCIALS_BASIS if is_financial else EBITDA_BASIS

    present = [m for m in ("revenue", "ebitda", "pbt", "pat") if metrics.get(m)]
    if len(present) < 2:
        raise ParseError(f"only {len(present)} of 4 metrics parsed: {present}")

    return {
        "period": periods.label(period_end),
        "period_end": period_end.isoformat(),
        "basis": basis,
        "units": f"₹ {units.group(1).lower()}" if units else None,
        "page": index + 1,
        "extracted_by": "pdfplumber",
        "ebitda_basis": ebitda_basis,
        "columns": {
            "current": periods.label(period_end),
            "prior_quarter": periods.label(periods.previous_quarter_end(period_end)),
            "year_ago": periods.label(periods.year_ago(period_end)),
        },
        "metrics": metrics,
    }


def _ebitda(found: dict[str, Any], cols: dict[str, int]) -> dict[str, Any] | None:
    """PBT + finance costs + depreciation - other income, per column.

    Verified against an independent source: for RELIANCE Q2 FY27 this gives 56,616,
    which is screener's reported operating profit for the same quarter to the rupee.
    """
    parts = {k: found.get(k) for k in ("pbt", "finance_costs", "depreciation", "other_income")}
    if parts["pbt"] is None or parts["finance_costs"] is None or parts["depreciation"] is None:
        return None

    values: dict[str, Any] = {}
    for name in cols:
        pbt = parts["pbt"].get(name)
        fin = parts["finance_costs"].get(name)
        dep = parts["depreciation"].get(name)
        if pbt is None or fin is None or dep is None:
            values[name] = None
            continue
        other = (parts["other_income"] or {}).get(name) or 0.0
        values[name] = round(pbt + fin + dep - other, 2)

    if values.get("current") is None:
        return None
    return {**values, "label": EBITDA_BASIS,
            "qoq_pct": pct(values["current"], values["prior_quarter"]),
            "yoy_pct": pct(values["current"], values["year_ago"])}

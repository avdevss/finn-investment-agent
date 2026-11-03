# Reads the quarterly and annual results tables off the cached screener page.
#
# These are the actuals the Promise Ledger is scored against. A results filing only
# ever carries the one quarter it reports, which cannot judge a commitment made a
# year ago; the quarterly table carries up to thirteen.
#
# Both tables are read here because they are the same shape: same row labels, same
# number parsing, same page. They differ only in which section they live in and in
# how a column header becomes a period label.
#
# Cross checked against the filing PDF for RELIANCE Q2 FY27: operating profit,
# profit before tax and net profit all agree to the rupee.

from __future__ import annotations

import argparse
import logging
from datetime import date
from typing import Any, Final

from bs4 import BeautifulSoup

from ingest import periods
from ingest.db import connect, upsert
from ingest.documents import screener_html
from ingest.results_table import parse_number
from ingest.seed_companies import COMPANIES, Company

log = logging.getLogger(__name__)

QUARTERLY_SOURCE: Final = "screener_quarters"
ANNUAL_SOURCE: Final = "screener_annual"

# Screener's own row labels. Lenders get a different set for the same idea, because
# interest is their cost of revenue rather than a financing line below it.
ROW_LABELS: Final[dict[str, tuple[str, ...]]] = {
    "sales": ("sales", "revenue"),
    "operating_profit": ("operating profit", "financing profit"),
    "opm": ("opm %", "financing margin %"),
    "other_income": ("other income",),
    "interest": ("interest",),
    "depreciation": ("depreciation",),
    "pbt": ("profit before tax",),
    "net_profit": ("net profit",),
}


def _label(cell: str) -> str:
    """Screener suffixes expandable rows with a '+'."""
    return " ".join(cell.replace("+", "").split()).strip().lower()


def _quarter_columns(dates: list[tuple[int, date | None]]) -> list[tuple[int, date]]:
    return [(i, d) for i, d in dates if d is not None and periods.is_quarter_end(d)]


def _annual_columns(dates: list[tuple[int, date | None]]) -> list[tuple[int, date]]:
    """March year ends only.

    A December year end would be labelled FY by the April to March convention this
    project uses everywhere else, which would read as the wrong fiscal year on a
    promise. Dropping the column and saying so beats mislabelling it.
    """
    kept = []
    for index, d in dates:
        if d is None:
            continue
        if d.month != 3:
            log.warning("skipping a %s year end column; FY labels assume March", d)
            continue
        kept.append((index, d))
    return kept


def parse(html: str, section_id: str = "quarters") -> list[dict[str, Any]]:
    """One row per period, oldest first. Empty when the section is missing."""
    annual = section_id != "quarters"
    section = BeautifulSoup(html, "lxml").find(id=section_id)
    if section is None or section.find("table") is None:
        return []

    rows = section.find("table").find_all("tr")
    if not rows:
        return []

    header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    # The first cell names the rows; every later cell is a period, except a trailing
    # TTM column that some companies carry.
    dates = [(i, periods.parse_date(text))
             for i, text in enumerate(header[1:], start=1)]
    columns = _annual_columns(dates) if annual else _quarter_columns(dates)
    if not columns:
        return []

    values: dict[str, dict[int, float | None]] = {}
    for tr in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if not cells:
            continue
        name = _label(cells[0])
        metric = next((m for m, labels in ROW_LABELS.items() if name in labels), None)
        if metric is None or metric in values:
            continue
        values[metric] = {i: (parse_number(cells[i]) if i < len(cells) else None)
                          for i, _ in columns}

    out: list[dict[str, Any]] = []
    for index, period_end in columns:
        row: dict[str, Any] = {
            "period_end": period_end,
            "period": (periods.fy_label if annual else periods.label)(period_end),
            "source": ANNUAL_SOURCE if annual else QUARTERLY_SOURCE}
        row.update({m: values.get(m, {}).get(index) for m in ROW_LABELS})
        if any(row[m] is not None for m in ROW_LABELS):
            out.append(row)
    return out


# (screener section, table, what to call it in the log)
TABLES: Final[tuple[tuple[str, str, str], ...]] = (
    ("quarters", "quarterly_results", "quarters"),
    ("profit-loss", "annual_results", "years"),
)


def load(conn, company: Company) -> int:
    html = screener_html(company.screener_slug)
    if html is None:
        return 0

    written = 0
    for section_id, table, noun in TABLES:
        rows = parse(html, section_id)
        if not rows:
            log.warning("%-11s no %s table on the screener page", company.symbol, noun)
            continue
        for row in rows:
            row["symbol"] = company.symbol
        written += upsert(conn, table, rows, ["symbol", "period_end"])
        log.info("%-11s %2d %-8s %s -> %s", company.symbol, len(rows), noun,
                 rows[0]["period"], rows[-1]["period"])
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Load quarterly and annual actuals")
    parser.add_argument("--only", action="append", help="limit to these symbols")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    targets = [c for c in COMPANIES if not args.only or c.symbol in set(args.only)]
    written = 0
    with connect() as conn:
        for company in targets:
            written += load(conn, company)
    log.info("wrote %d actuals rows", written)


if __name__ == "__main__":
    main()

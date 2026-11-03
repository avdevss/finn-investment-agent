# Resolves each company's screener page and records its recent concall transcripts.
#
# The page is fetched once per company for the whole build and cached on disk, so
# quarters.py reads the same copy rather than hitting screener again.
#
# Four transcripts, not one: a promise made three quarters ago needs a closed period
# to be judged against, and with only the latest call every promise in the ledger
# would still be open.

from __future__ import annotations

import argparse
import logging
import pathlib
from dataclasses import dataclass
from datetime import date
from typing import Final

import requests
from bs4 import BeautifulSoup

from ingest import pdf, periods, speakers
from ingest.db import connect, upsert
from ingest.http import SCREENER_COMPANY_URL, SCREENER_HEADERS, get
from ingest.seed_companies import COMPANIES, Company

log = logging.getLogger(__name__)

SCREENER_DIR: Final = pdf.CACHE / "screener"
TRANSCRIPTS_PER_COMPANY: Final = 4
PROJECT_ROOT: Final = pathlib.Path(__file__).parent.parent

DOC_CONCALL: Final = "concall"


@dataclass(frozen=True)
class Concall:
    held_on: date           # the month the call was held, per the screener listing
    period_end: date        # the quarter it reports on
    period: str
    url: str


def screener_html(slug: str) -> str | None:
    """The company page, fetched once and cached on disk."""
    path = SCREENER_DIR / f"{slug}.html"
    if path.exists():
        return path.read_text()

    try:
        body = get(SCREENER_COMPANY_URL.format(symbol=slug),
                   headers=SCREENER_HEADERS, timeout=45).text
    except requests.RequestException as e:
        log.warning("%s: screener fetch failed, %s: %s", slug, type(e).__name__, e)
        return None

    SCREENER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".html.tmp")
    tmp.write_text(body)
    tmp.replace(path)
    return body


def _section(soup, heading: str):
    docs = soup.find(id="documents")
    if docs is None:
        return None
    for tag in docs.find_all("h3"):
        if heading.lower() in tag.get_text(" ", strip=True).lower():
            return tag.find_next("ul", class_="list-links")
    return None


def concalls(html: str, limit: int = TRANSCRIPTS_PER_COMPANY) -> list[Concall]:
    """The most recent transcripts, newest first, one per reporting period.

    Each entry reads "Nov 2026 | Transcript | AI Summary | PPT", so the month of the
    call gives the quarter it reports on. The list carries repeats of the same month,
    so periods are deduplicated.
    """
    listing = _section(BeautifulSoup(html, "lxml"), "concall")
    if listing is None:
        return []

    found: dict[date, Concall] = {}
    for item in listing.select("li"):
        link = next((a for a in item.find_all("a", href=True)
                     if a.get_text(" ", strip=True).lower() == "transcript"), None)
        if link is None:
            continue

        when = periods.parse_date(item.get_text(" ", strip=True)[:40])
        if when is None:
            continue
        period_end = periods.reported_quarter_end(when.year, when.month)
        if period_end in found:
            continue
        found[period_end] = Concall(when, period_end, periods.label(period_end),
                                    link["href"])

    return sorted(found.values(), key=lambda c: c.period_end, reverse=True)[:limit]


def _existing(conn, symbol: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("select period from documents where symbol = %s and doc_type = %s",
                    (symbol, DOC_CONCALL))
        return {r[0] for r in cur.fetchall()}


def load(conn, company: Company, force: bool = False) -> int:
    """Download and record this company's recent transcripts. Returns rows written."""
    html = screener_html(company.screener_slug)
    if html is None:
        return 0

    items = concalls(html)
    if not items:
        log.warning("%-11s no concall transcripts on the screener page", company.symbol)
        return 0

    have = set() if force else _existing(conn, company.symbol)
    rows = []

    for item in items:
        if item.period in have:
            continue
        got = pdf.fetch_text(item.url)
        if got is None:
            log.warning("%-11s %s transcript unusable", company.symbol, item.period)
            continue

        digest, extracted = got
        attributed = speakers.attribute(extracted.text)
        rows.append({
            "id": digest,
            "symbol": company.symbol,
            "doc_type": DOC_CONCALL,
            "period": item.period,
            "url": item.url,
            "doc_date": item.held_on,
            "text_path": str((pdf.TEXT_DIR / f"{digest}.txt").relative_to(PROJECT_ROOT)),
            "pages": extracted.pages,
            "char_count": extracted.char_count,
            "speaker_mode": attributed.mode,
            "mgmt_chars": attributed.mgmt_chars,
        })
        log.info("%-11s %-7s %3d pages, %6d chars, %-11s %6d mgmt chars",
                 company.symbol, item.period, extracted.pages, extracted.char_count,
                 attributed.mode, attributed.mgmt_chars)

    return upsert(conn, "documents", rows, ["id"]) if rows else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Load concall transcripts")
    parser.add_argument("--only", action="append", help="limit to these symbols")
    parser.add_argument("--force", action="store_true", help="re-download")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    targets = [c for c in COMPANIES
               if not args.only or c.symbol in set(args.only)]
    written = 0
    with connect() as conn:
        for company in targets:
            written += load(conn, company, args.force)
    log.info("wrote %d transcript documents", written)


if __name__ == "__main__":
    main()

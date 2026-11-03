# Loads 30 days of BSE announcements per company, classifies and scores each one,
# and writes them with the score breakdown attached.
#
# Paging is not optional: the endpoint returns 50 rows per page and the window
# total in Table1[0].ROWCNT, so stopping at page one silently drops the older
# filings in the window.

from __future__ import annotations

import argparse
import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Any, Final

from ingest import classify
from ingest.db import connect, upsert
from ingest.http import BSE_ANN_URL, BSE_API_HEADERS, BSE_PDF_BASE, get_json
from ingest.materiality import Scorer
from ingest.seed_companies import COMPANIES, Company
from ingest.trading_days import IST

log = logging.getLogger(__name__)

PAGE_SIZE: Final = 50
MAX_PAGES: Final = 20
DEFAULT_DAYS: Final = 30


def _ident(symbol: str, newsid: str | None, published_at: datetime, title: str) -> str:
    key = f"{symbol}|{newsid}" if newsid else f"{symbol}|{published_at.isoformat()}|{title}"
    return hashlib.sha256(key.encode()).hexdigest()


def fetch(scrip: str, since: date, until: date) -> list[dict[str, Any]]:
    """Every announcement in the window, paging to ROWCNT."""
    rows: list[dict[str, Any]] = []
    total: int | None = None

    for page in range(1, MAX_PAGES + 1):
        body = get_json(BSE_ANN_URL, headers=BSE_API_HEADERS, params={
            "pageno": page, "strCat": "-1", "subcategory": "-1",
            "strPrevDate": since.strftime("%Y%m%d"), "strToDate": until.strftime("%Y%m%d"),
            "strScrip": scrip, "strSearch": "P", "strType": "C",
        })
        if isinstance(body, str):          # retired endpoints answer with a bare string
            log.warning("scrip %s: endpoint returned %r", scrip, body[:60])
            break

        batch = body.get("Table") or []
        if not batch:
            break
        rows += batch

        if total is None:
            table1 = body.get("Table1") or [{}]
            total = int(table1[0].get("ROWCNT") or 0) or None
        if total is not None and len(rows) >= total:
            break
        if len(batch) < PAGE_SIZE:
            break
    else:
        log.warning("scrip %s: hit the %d page ceiling", scrip, MAX_PAGES)

    if total is not None and len(rows) != total:
        log.warning("scrip %s: collected %d rows but ROWCNT said %d", scrip, len(rows), total)
    return rows


def _published_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=IST)
    except ValueError:
        log.warning("unparseable NEWS_DT %r", raw)
        return None


def build(company: Company, raw: list[dict[str, Any]],
          scorer: Scorer) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    needs_model: list[int] = []

    for item in raw:
        title = (item.get("NEWSSUB") or "").strip()
        published_at = _published_at(item.get("NEWS_DT"))
        if not title or published_at is None:
            continue

        category = classify.by_rules(title)
        classified_by = "rules"
        if category is None:
            category = classify.by_bse(item.get("CATEGORYNAME"), item.get("SUBCATNAME"))
            classified_by = "bse_category"
        if category is None and classify.is_ambiguous(title, company.name):
            # Boilerplate title, and BSE's subcategory was no better. Hold it at
            # routine weight rather than letting the model guess from nothing; the
            # filing text resolves it.
            category, classified_by = "unclassified", classify.AMBIGUOUS_BY
        if category is None:
            classified_by = "pending"

        attachment = (item.get("ATTACHMENTNAME") or "").strip()
        newsid = (str(item.get("NEWSID")) or "").strip() or None

        rows.append({
            "id": _ident(company.symbol, newsid, published_at, title),
            "symbol": company.symbol,
            "bse_newsid": newsid,
            "published_at": published_at,
            "title": title,
            "category": category or "unclassified",
            "bse_category": (item.get("SUBCATNAME") or item.get("CATEGORYNAME") or "").strip() or None,
            "pdf_url": BSE_PDF_BASE + attachment if attachment else None,
            "materiality": 0,
            "score_parts": {},
            "summary": None,
            "extracted": None,
            "classified_by": classified_by,
            "pdf_sha256": None,
        })
        if classified_by == "pending":
            needs_model.append(len(rows) - 1)

    if needs_model:
        labels = classify.by_model([rows[i]["title"] for i in needs_model])
        for position, row_index in enumerate(needs_model):
            label = labels.get(position)
            if label is None:
                rows[row_index]["classified_by"] = "unresolved"
                continue
            rows[row_index]["category"] = label[0]
            rows[row_index]["classified_by"] = label[1]

    for row in rows:
        row["materiality"], row["score_parts"] = scorer.apply(
            company.symbol, row["published_at"], row["category"])

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Load BSE announcements")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    until = date.today()
    since = until - timedelta(days=args.days)
    written = 0

    with connect() as conn:
        scorer = Scorer.load(conn)

        for company in COMPANIES:
            if not company.bse_scrip:
                log.info("%-11s skipped, no BSE scrip", company.symbol)
                continue

            raw = fetch(company.bse_scrip, since, until)
            rows = build(company, raw, scorer)
            if rows:
                written += upsert(conn, "announcements", rows, ["id"])
            top = max((r["materiality"] for r in rows), default=0)
            log.info("%-11s %3d filings, top materiality %d", company.symbol, len(rows), top)

    log.info("wrote %d announcements over %d days", written, args.days)


if __name__ == "__main__":
    main()

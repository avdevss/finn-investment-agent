# Every pass that needs the filing PDF itself, in one walk over the announcements
# table, so a document is fetched and extracted once and then used three times:
#
#   re-classify  -> for titles that said nothing and for title-only model guesses
#   extract      -> the results table, deterministically, model only on failure
#   rescore      -> a new category weight, or the notice cap, has to reach materiality
#   summarise    -> one line, only for what still reaches the card tier
#
# Extraction runs before the summary on purpose: a results notice is capped once the
# filing confirms it carries no statement, which drops it out of the card tier and
# saves the summary call entirely.
#
# Each stage skips work already done, so an interrupted run resumes where it stopped.

from __future__ import annotations

import argparse
import logging
from typing import Any, Final

import psycopg
from psycopg.types.json import Jsonb

from ingest import classify, materiality, pdf, results_table
from ingest.db import connect
from ingest.llm import LLMError, generate_json
from ingest.materiality import CARD_THRESHOLD, Scorer
from ingest.seed_companies import FINANCIAL_SECTORS

log = logging.getLogger(__name__)

CLASSIFY_PAGES: Final = 1
SUMMARY_PAGES: Final = 2
SUMMARY_CHARS: Final = 6000

_SUMMARY_SCHEMA: Final = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}
_SUMMARY_SYSTEM: Final = (
    "You write a one sentence summary of an Indian stock exchange filing for a "
    "professional investor. Use only the filing text provided. Do not use outside "
    "knowledge about the company and do not state anything the text does not say. "
    "Lead with what happened and the number that matters. No preamble, no company "
    "boilerplate, at most 30 words. If the text does not say what happened, reply "
    "with an empty string."
)

_RESULTS_SCHEMA: Final = {
    "type": "object",
    "properties": {
        "period_end": {"type": "string"},
        "units": {"type": "string"},
        "metrics": {
            "type": "object",
            "properties": {
                # Gemini's responseSchema takes `nullable`, not a JSON Schema
                # type union; a union is rejected with a 400.
                m: {"type": "object",
                    "properties": {c: {"type": "number", "nullable": True}
                                   for c in results_table.COLUMNS},
                    "required": list(results_table.COLUMNS)}
                for m in ("revenue", "ebitda", "pbt", "pat")
            },
        },
    },
    "required": ["metrics"],
}
_RESULTS_SYSTEM: Final = (
    "You read the profit and loss statement in an Indian quarterly results filing. "
    "Use only the text provided; never use outside knowledge and never estimate. "
    "Report revenue, EBITDA, profit before tax and profit after tax for three "
    "periods: the quarter being reported ('current'), the quarter before it "
    "('prior_quarter'), and the same quarter one year earlier ('year_ago'). Copy the "
    "figures exactly as printed, in the statement's own units. Use null for anything "
    "the statement does not print. If the filing contains both a consolidated and a "
    "standalone statement, read the consolidated one."
)


def _summarise(body: str) -> str | None:
    try:
        parsed, _ = generate_json(
            "Summarise this filing in one sentence.\n\n--- filing text ---\n" + body,
            schema=_SUMMARY_SCHEMA, tier="cheap", system=_SUMMARY_SYSTEM, max_tokens=256)
    except LLMError as e:
        log.error("summary call failed: %s", e)
        return None
    line = " ".join((parsed.get("summary") or "").split())
    return line or None


def _extract_by_model(pages: list[str], reason: str) -> dict[str, Any] | None:
    """Fallback for a statement the deterministic parser could not read."""
    body = "\n".join(pages)[:40000]
    try:
        parsed, meta = generate_json(
            "Read the profit and loss statement.\n\n--- filing text ---\n" + body,
            schema=_RESULTS_SCHEMA, tier="cheap", system=_RESULTS_SYSTEM, max_tokens=1024)
    except LLMError as e:
        log.error("results fallback call failed: %s", e)
        return None

    metrics = parsed.get("metrics") or {}
    if not any(metrics.get(m, {}).get("current") is not None
               for m in ("revenue", "pbt", "pat")):
        log.warning("results fallback returned nothing usable")
        return None

    for figures in metrics.values():
        figures["qoq_pct"] = results_table.pct(figures.get("current"),
                                               figures.get("prior_quarter"))
        figures["yoy_pct"] = results_table.pct(figures.get("current"),
                                               figures.get("year_ago"))
    return {
        "period": None, "period_end": parsed.get("period_end"),
        "basis": "unstated", "units": parsed.get("units"), "page": None,
        "extracted_by": meta.model_used, "fallback_reason": reason,
        "ebitda_basis": None, "columns": None, "metrics": metrics,
    }


def _results(pages: list[str], is_financial: bool) -> dict[str, Any]:
    """The statement, or a recorded absence.

    Plenty of filings classified `results` are notices rather than numbers: a board
    meeting intimation carries no statement to read. That outcome is written down so
    the row stops asking to be re-read on every run.
    """
    try:
        return results_table.extract(pages, is_financial=is_financial)
    except results_table.ParseError as e:
        reason = str(e)
    log.info("deterministic parse failed (%s), trying the model", reason)

    payload = _extract_by_model(pages, reason)
    return payload if payload is not None else {
        "status": "unavailable",
        "reason": "no profit and loss statement found in the filing",
        "extracted_by": None,
        "metrics": None,
    }


def _pending(conn: psycopg.Connection, symbols: list[str] | None,
             force: bool) -> list[dict[str, Any]]:
    where = ["a.pdf_url is not null"]
    params: list[Any] = []
    if symbols:
        where.append("a.symbol = any(%s)")
        params.append(symbols)
    if not force:
        # The last clause is the notice cap catching up with rows whose PDF was read
        # before the cap existed: the statement is known to be absent and no cap has
        # been considered. It needs no network, only a rescore.
        where.append("""(
            a.classified_by not in ('rules', 'bse_category')
              and a.classified_by not like 'pdf_text:%%'
            or (a.materiality >= %s and a.summary is null)
            or (a.category = 'results' and a.extracted is null)
            or (a.category = 'results' and a.extracted is not null
                and a.extracted->>'metrics' is null
                and a.score_parts->'notice_cap' is null)
        )""")
        params.append(CARD_THRESHOLD)

    with conn.cursor() as cur:
        cur.execute(f"""select a.id, a.symbol, a.published_at, a.title, a.category,
                               a.materiality, a.summary, a.extracted, a.classified_by,
                               a.score_parts, a.pdf_url, c.sector
                        from announcements a join companies c on c.symbol = a.symbol
                        where {' and '.join(where)}
                        order by a.published_at desc""", params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _rescore(row: dict[str, Any], scorer: Scorer,
             updates: dict[str, Any]) -> None:
    """Rebuild the score from the row's current category and notice state."""
    notice = materiality.notice_capped(row["category"], row["title"], row["extracted"])
    total, parts = scorer.apply(row["symbol"], row["published_at"],
                                row["category"], notice)
    row["materiality"] = total
    row["score_parts"] = parts
    updates["materiality"] = total
    updates["score_parts"] = parts


def _wants_summary(row: dict[str, Any], force: bool) -> bool:
    return force or (row["materiality"] >= CARD_THRESHOLD and row["summary"] is None)


def process(conn: psycopg.Connection, row: dict[str, Any], scorer: Scorer,
            force: bool) -> dict[str, int]:
    """Run whatever stages this filing still needs. Returns what it did."""
    did = {"reclassified": 0, "summarised": 0, "extracted": 0, "capped": 0,
           "skipped_no_pdf": 0}

    settled = row["classified_by"] in ("rules", "bse_category")
    already_read = str(row["classified_by"]).startswith(classify.PDF_BY + ":")
    want_class = force or (not settled and not already_read)
    want_results = force or (row["category"] == "results" and row["extracted"] is None)
    want_summary = _wants_summary(row, force)

    # A cap the filing already decided needs no PDF, only arithmetic.
    capped_now = materiality.notice_capped(row["category"], row["title"],
                                           row["extracted"])
    want_cap = capped_now and "notice_cap" not in (row["score_parts"] or {})

    if not (want_class or want_results or want_summary or want_cap):
        return did

    updates: dict[str, Any] = {}

    if want_class or want_results or want_summary:
        got = pdf.fetch_text(row["pdf_url"]) if row.get("pdf_url") else None
        if got is None:
            did["skipped_no_pdf"] = 1
            return did
        digest, extracted_text = got
        updates["pdf_sha256"] = digest

        if want_class:
            labelled = classify.by_pdf_text(extracted_text.first_pages(CLASSIFY_PAGES))
            if labelled is not None:
                category, by = labelled
                if category != row["category"]:
                    log.info("%-11s %-17s -> %-17s %s", row["symbol"], row["category"],
                             category, row["title"][:52])
                row["category"] = category
                updates["category"] = category
                updates["classified_by"] = by
                did["reclassified"] = 1
                _rescore(row, scorer, updates)
                want_results = force or (category == "results"
                                         and row["extracted"] is None)

        if want_results:
            payload = _results(extracted_text.page_list(),
                               row["sector"] in FINANCIAL_SECTORS)
            updates["extracted"] = payload
            row["extracted"] = payload
            did["extracted"] = int(payload.get("metrics") is not None)
            want_cap = materiality.notice_capped(row["category"], row["title"], payload)

        if want_cap:
            _rescore(row, scorer, updates)
            did["capped"] = 1
            log.info("%-11s results notice capped to %2d  %s", row["symbol"],
                     row["materiality"], row["title"][:52])

        # The cap can only lower the score, so a summary wanted before it may no
        # longer be worth the call.
        if _wants_summary(row, force):
            summary = _summarise(
                extracted_text.first_pages(SUMMARY_PAGES)[:SUMMARY_CHARS])
            if summary:
                updates["summary"] = summary
                did["summarised"] = 1
    else:
        _rescore(row, scorer, updates)
        did["capped"] = 1
        log.info("%-11s results notice capped to %2d  %s", row["symbol"],
                 row["materiality"], row["title"][:52])

    _update(conn, row["id"], updates)
    return did


def _update(conn: psycopg.Connection, ident: str, updates: dict[str, Any]) -> None:
    sets, values = [], []
    for column, value in updates.items():
        sets.append(f"{column} = %s")
        values.append(Jsonb(value) if isinstance(value, (dict, list)) else value)
    values.append(ident)
    with conn.cursor() as cur:
        cur.execute(f"update announcements set {', '.join(sets)} where id = %s", values)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF derived passes over announcements")
    parser.add_argument("--only", action="append", help="limit to these symbols")
    parser.add_argument("--force", action="store_true", help="redo finished stages")
    parser.add_argument("--limit", type=int, help="stop after this many filings")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    totals = {"reclassified": 0, "summarised": 0, "extracted": 0, "capped": 0,
              "skipped_no_pdf": 0}
    with connect() as conn:
        scorer = Scorer.load(conn)
        rows = _pending(conn, args.only, args.force)
        if args.limit:
            rows = rows[:args.limit]
        log.info("%d filings need a PDF pass", len(rows))

        for n, row in enumerate(rows, 1):
            for key, value in process(conn, row, scorer, args.force).items():
                totals[key] += value
            if n % 20 == 0:
                log.info("  %d/%d", n, len(rows))

    log.info("reclassified %d, results extracted %d, notices capped %d, "
             "summarised %d, no usable PDF %d",
             *(totals[k] for k in ("reclassified", "extracted", "capped",
                                   "summarised", "skipped_no_pdf")))


if __name__ == "__main__":
    main()

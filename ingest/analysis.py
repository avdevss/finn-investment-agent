# Business snapshot and bull versus bear for the company page, one strong model call
# each, written with the token count it cost.
#
# The only inputs are the company's own transcript and its own reported numbers. The
# prompt forbids outside knowledge, so nothing on the page comes from what a model
# happens to remember about the company.
#
# This is the only caller of the strong tier, which on the free tier is 20 requests
# per day for one model. There are 22 companies, so a full build does not fit in one
# day: the research names go first because the research screen is the one an evaluator
# can point anywhere, then the portfolio, and whatever the budget does not reach is
# named in the log as pending for the next reset. Nothing degrades silently.
#
# The live research route passes --live and runs on the cheap tier instead, because a
# handful of ad hoc searches would spend the whole day's strong budget. Those rows are
# marked so the page can say which tier produced them.

from __future__ import annotations

import argparse
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Final

import psycopg

from ingest import speakers
from ingest.db import connect, upsert
from ingest.llm import LLMError, generate_json, usage
from ingest.seed_companies import COMPANIES, Company

log = logging.getLogger(__name__)

TRANSCRIPT_CHARS: Final = 60_000

# Free tier generate-content requests per day, per model. Cached answers do not spend
# it, so the budget is counted in calls actually sent.
STRONG_DAILY_CALLS: Final = 20

_SCHEMA: Final = {
    "type": "object",
    "properties": {
        "snapshot": {"type": "string"},
        "how_it_makes_money": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "note": {"type": "string"}},
                "required": ["name", "note"],
            },
        },
        "financial_read": {"type": "string"},
        "bull": {"type": "array", "items": {"type": "string"}},
        "bear": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["snapshot", "how_it_makes_money", "financial_read", "bull", "bear"],
}

_SYSTEM: Final = (
    "You brief a professional investor on an Indian listed company, using only the "
    "two sources given to you: an extract of the company's own earnings call "
    "transcript, and its own reported quarterly figures.\n"
    "Hard rules:\n"
    "1. Use only these sources. Do not use anything you know about the company from "
    "elsewhere. Do not add a fact, a number, a competitor or a market size that is "
    "not in the text.\n"
    "2. Every number you state must appear in the sources.\n"
    "3. If the sources do not support a point, leave it out. A short brief is better "
    "than a padded one.\n"
    "4. No hedging filler and no investment advice. Write plainly.\n"
    "Return: snapshot (2 sentences on what the business is), how_it_makes_money "
    "(1-2 sentences), segments (the operating segments the call discusses, with one "
    "line each), financial_read (3-4 sentences on what the reported numbers show), "
    "bull (3-4 specific points the sources support), bear (3-4 specific risks or "
    "weaknesses the sources support, including anything management was pressed on)."
)


def _inputs(conn: psycopg.Connection, symbol: str) -> tuple[str, str] | None:
    """(transcript extract, numbers table) or None when there is nothing to read."""
    with conn.cursor() as cur:
        cur.execute("""select period, text_path from documents
                       where symbol = %s and doc_type = 'concall'
                       order by doc_date desc nulls last limit 1""", (symbol,))
        latest = cur.fetchone()

        cur.execute("""select period, sales, operating_profit, opm, pbt, net_profit
                       from quarterly_results where symbol = %s
                       order by period_end desc limit 8""", (symbol,))
        quarters = cur.fetchall()

    if latest is None and not quarters:
        return None

    transcript = ""
    if latest is not None:
        path = pathlib.Path(latest[1])
        if not path.is_absolute():
            path = pathlib.Path(__file__).parent.parent / path
        if path.exists():
            body = speakers.attribute(path.read_text()).management_text
            transcript = f"Earnings call, {latest[0]}:\n{body[:TRANSCRIPT_CHARS]}"

    header = f"{'period':<9}{'sales':>12}{'op profit':>12}{'opm %':>8}{'pbt':>12}{'net':>12}"
    lines = [header]
    for period, sales, op, opm, pbt, net in reversed(quarters):
        cells = "".join(f"{('-' if v is None else f'{float(v):,.0f}'):>12}"
                        for v in (sales, op))
        lines.append(f"{period:<9}{cells}{('-' if opm is None else f'{float(opm):,.0f}'):>8}"
                     + "".join(f"{('-' if v is None else f'{float(v):,.0f}'):>12}"
                               for v in (pbt, net)))
    return transcript, "\n".join(lines)


def build(conn: psycopg.Connection, symbol: str, name: str, sector: str,
          live: bool = False) -> dict[str, Any] | None:
    got = _inputs(conn, symbol)
    if got is None:
        log.warning("%-11s no transcript and no numbers, skipping", symbol)
        return None
    transcript, numbers = got

    prompt = (f"Company: {name} ({symbol}), sector {sector}.\n\n"
              f"--- source 1: reported quarterly figures (the company's own) ---\n"
              f"{numbers}\n\n"
              f"--- source 2: the company's own earnings call ---\n{transcript}")
    try:
        parsed, meta = generate_json(prompt, schema=_SCHEMA,
                                     tier="cheap" if live else "strong",
                                     system=_SYSTEM, max_tokens=4096)
    except LLMError as e:
        log.error("%-11s analysis call failed: %s", symbol, e)
        return None

    return {
        "symbol": symbol,
        "payload": parsed,
        "model_used": meta.model_used,
        # True when the cheap tier produced this, which is how the live research
        # route runs. The company page says so rather than implying parity.
        "degraded": meta.tier_requested == "cheap",
        "prompt_tokens": meta.prompt_tokens,
        "output_tokens": meta.output_tokens,
        "built_at": datetime.now(timezone.utc),
    }


def _done(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("select symbol from analysis")
        return {r[0] for r in cur.fetchall()}


def _order(targets: list[Company]) -> list[Company]:
    """Research names first: that screen is the one an evaluator points anywhere."""
    return sorted(targets, key=lambda c: c.in_portfolio)


def _strong_calls_spent() -> int:
    return sum(n for model, n in usage.calls_by_model.items()
               if model == os.getenv("STRONG_MODEL", "").strip())


def show(conn: psycopg.Connection, symbol: str) -> None:
    with conn.cursor() as cur:
        cur.execute("""select payload, model_used, degraded, prompt_tokens, output_tokens
                       from analysis where symbol = %s""", (symbol,))
        row = cur.fetchone()
    if row is None:
        print(f"no analysis for {symbol}")
        return

    payload, model, degraded, ptok, otok = row
    print(f"\n{'=' * 78}\n{symbol} - {model}{' (cheap tier)' if degraded else ''}, "
          f"{ptok:,} prompt + {otok:,} output tokens\n{'=' * 78}")
    print(f"\nSNAPSHOT\n  {payload.get('snapshot', '')}")
    print(f"\nHOW IT MAKES MONEY\n  {payload.get('how_it_makes_money', '')}")
    for segment in payload.get("segments") or []:
        print(f"  - {segment.get('name')}: {segment.get('note')}")
    print(f"\nFINANCIAL READ\n  {payload.get('financial_read', '')}")
    for side in ("bull", "bear"):
        print(f"\n{side.upper()}")
        for point in payload.get(side) or []:
            print(f"  - {point}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build company analysis")
    parser.add_argument("--only", action="append", help="limit to these symbols")
    parser.add_argument("--show", metavar="SYMBOL", help="print one and stop")
    parser.add_argument("--force", action="store_true", help="rebuild finished ones")
    parser.add_argument("--live", action="store_true",
                        help="run on the cheap tier, as the research route does")
    parser.add_argument("--max-calls", type=int, default=STRONG_DAILY_CALLS,
                        help="stop after this many strong requests actually sent")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with connect() as conn:
        if args.show:
            show(conn, args.show.upper())
            return

        targets = _order([c for c in COMPANIES
                          if not args.only or c.symbol in set(args.only)])
        have = set() if args.force else _done(conn)
        written = 0
        pending: list[str] = []

        for company in targets:
            if company.symbol in have:
                continue
            if not args.live and _strong_calls_spent() >= args.max_calls:
                pending.append(company.symbol)
                continue

            row = build(conn, company.symbol, company.name, company.sector, args.live)
            if row is None:
                pending.append(company.symbol)
                continue
            written += upsert(conn, "analysis", [row], ["symbol"])
            log.info("%-11s %s%s, %d + %d tokens", company.symbol, row["model_used"],
                     " (cheap tier)" if row["degraded"] else "",
                     row["prompt_tokens"], row["output_tokens"])

        log.info("built analysis for %d companies, %d strong requests sent",
                 written, _strong_calls_spent())
        if pending:
            log.warning("pending, rerun after the daily quota resets: %s",
                        ", ".join(pending))


if __name__ == "__main__":
    main()

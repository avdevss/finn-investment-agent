# Postgres access for the whole ingest side: one connection helper, one upsert
# helper every writer uses, and the --counts report that proves a run worked.

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Final

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

load_dotenv()

log = logging.getLogger(__name__)

SCHEMA_PATH: Final = pathlib.Path(__file__).parent.parent / "schema.sql"


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set in .env")
    with psycopg.connect(url, connect_timeout=30) as conn:
        yield conn


def apply_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA_PATH.read_text())
        conn.commit()


def _adapt(value: Any) -> Any:
    return Jsonb(value) if isinstance(value, (dict, list)) else value


def upsert(conn: psycopg.Connection, table: str, rows: Sequence[dict[str, Any]],
           conflict: Sequence[str]) -> int:
    """Insert rows, updating every non-key column on conflict. Returns rows written."""
    if not rows:
        return 0

    columns = list(rows[0].keys())
    if any(set(r.keys()) != set(columns) for r in rows):
        raise ValueError(f"{table}: all rows must carry the same columns")

    updates = [c for c in columns if c not in conflict]
    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    action = (f"update set {', '.join(f'{c} = excluded.{c}' for c in updates)}"
              if updates else "nothing")
    sql = (f"insert into {table} ({col_list}) values ({placeholders}) "
           f"on conflict ({', '.join(conflict)}) do {action}")

    with conn.cursor() as cur:
        cur.executemany(sql, [[_adapt(r[c]) for c in columns] for r in rows])
    conn.commit()
    return len(rows)


def _counts() -> None:
    queries: list[tuple[str, str]] = [
        ("companies", "select count(*) || ' rows, ' || count(*) filter (where in_portfolio) "
                      "|| ' in portfolio' from companies"),
        ("prices", "select coalesce(count(*) || ' rows, ' || count(distinct symbol) || ' symbols, ' "
                   "|| count(distinct date) || ' trading days, ' || min(date) || ' -> ' || max(date), "
                   "'empty') from prices"),
        ("announcements", "select coalesce(count(*) || ' rows, ' || count(distinct symbol) "
                          "|| ' symbols, ' || min(published_at)::date || ' -> ' "
                          "|| max(published_at)::date, 'empty') from announcements"),
        ("tier >=60 card", "select count(*) from announcements where materiality >= 60"),
        ("tier 30-59 row", "select count(*) from announcements where materiality between 30 and 59"),
        ("tier <30 collapsed", "select count(*) from announcements where materiality < 30"),
        ("classified by rules", "select count(*) from announcements where classified_by = 'rules'"),
        ("classified by bse", "select count(*) from announcements where classified_by = 'bse_category'"),
        ("classified by model", "select count(*) from announcements where classified_by "
                                "not in ('rules','bse_category','unresolved','ambiguous_title') "
                                "and classified_by not like 'pdf_text:%'"),
        ("re-read from the PDF", "select count(*) from announcements "
                                 "where classified_by like 'pdf_text:%'"),
        ("ambiguous title", "select count(*) from announcements "
                            "where classified_by = 'ambiguous_title'"),
        ("classifier unresolved", "select count(*) from announcements where classified_by = 'unresolved'"),
        ("priced same day", "select count(*) from announcements "
                            "where score_parts->>'price_basis' = 'same_day'"),
        ("priced next trading day", "select count(*) from announcements "
                                    "where score_parts->>'price_basis' = 'next_trading_day'"),
        ("no price day", "select count(*) from announcements "
                         "where score_parts->>'price_basis' like 'unavailable%'"),
        ("summaries", "select count(*) from announcements where summary is not null"),
        ("results extracted", "select count(*) from announcements "
                              "where extracted->>'extracted_by' is not null"),
        ("results unavailable", "select count(*) from announcements "
                                "where extracted->>'status' = 'unavailable'"),
        ("documents", "select coalesce(count(*) || ' rows, ' || count(distinct symbol) "
                      "|| ' symbols, ' || min(doc_date) || ' -> ' || max(doc_date), 'empty') "
                      "from documents"),
        ("transcript speakers", "select coalesce(string_agg(speaker_mode || '=' || n, ', '), "
                                "'none') from (select speaker_mode, count(*) n from documents "
                                "group by 1 order by 2 desc) t"),
        ("quarterly_results", "select coalesce(count(*) || ' rows, ' || count(distinct symbol) "
                              "|| ' symbols, ' || min(period_end) || ' -> ' || max(period_end), "
                              "'empty') from quarterly_results"),
        ("annual_results", "select coalesce(count(*) || ' rows, ' || count(distinct symbol) "
                           "|| ' symbols, ' || min(period) || ' -> ' || max(period), "
                           "'empty') from annual_results"),
        ("promises", "select coalesce(count(*) || ' rows, ' || count(distinct symbol) "
                     "|| ' symbols', 'empty') from promises"),
        ("promises by status", "select coalesce(string_agg(status || '=' || n, ', '), 'none') "
                               "from (select status, count(*) n from promises group by 1 "
                               "order by 2 desc) t"),
        ("promises scored", "select count(*) from promises where checked_at is not null"),
        ("promises by model", "select coalesce(string_agg(model_used || '=' || n, ', '), "
                              "'none') from (select coalesce(model_used, 'none') model_used, "
                              "count(*) n from promises group by 1 order by 2 desc) t"),
        ("analysis", "select coalesce(count(*) || ' companies, ' || sum(prompt_tokens) "
                     "|| ' prompt + ' || sum(output_tokens) || ' output tokens', 'empty') "
                     "from analysis"),
        ("analysis by model", "select coalesce(string_agg(model_used || '=' || n, ', '), "
                              "'none') from (select model_used, count(*) n from analysis "
                              "group by 1 order by 2 desc) t"),
        ("analysis cheap tier", "select count(*) from analysis where degraded"),
    ]
    with connect() as conn, conn.cursor() as cur:
        for label, sql in queries:
            cur.execute(sql)
            print(f"{label:<24} {cur.fetchone()[0]}")

        cur.execute("select kind, threshold, categories, enabled from alert_rules order by kind")
        for kind, threshold, categories, enabled in cur.fetchall():
            detail = threshold if threshold is not None else json.dumps(categories)
            print(f"{'rule ' + kind:<24} {detail}  enabled={enabled}")

        cur.execute("select category, count(*) from announcements group by 1 order by 2 desc limit 6")
        top = ", ".join(f"{c}={n}" for c, n in cur.fetchall())
        print(f"{'top categories':<24} {top or 'none'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="FINN database utilities")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply-schema", action="store_true", help="run schema.sql")
    group.add_argument("--counts", action="store_true", help="report what the ingest wrote")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.apply_schema:
        apply_schema()
        print(f"applied {SCHEMA_PATH.name}")
    else:
        _counts()


if __name__ == "__main__":
    main()

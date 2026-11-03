// Company page data: price history, quarterly actuals, the Promise Ledger and the analysis.
import "server-only";
import { sql } from "./db";
import type { Analysis, Extracted, PromiseRow } from "./types";

export type Company = { symbol: string; name: string; sector: string | null; in_portfolio: boolean };
export type PricePoint = { date: string; close: number; volume: number; pct_change: number | null };
export type Quarter = {
  period: string; period_end: string; sales: number | null; operating_profit: number | null;
  opm: number | null; pbt: number | null; net_profit: number | null;
};
export type LatestResults = { published_at: Date; pdf_url: string | null; extracted: Extracted };

export async function getCompany(symbol: string): Promise<Company | null> {
  const [row] = await sql<Company[]>`
    select symbol, name, sector, in_portfolio from companies where symbol = ${symbol}`;
  return row ?? null;
}

export async function getPrices(symbol: string): Promise<PricePoint[]> {
  return sql<PricePoint[]>`
    select date::text as date, close::float8 as close, volume::float8 as volume,
           pct_change::float8 as pct_change
    from prices
    where symbol = ${symbol}
      and date > (select max(date) from prices where symbol = ${symbol}) - interval '1 year'
    order by date`;
}

// Five quarters: the latest, the one before, and the same quarter a year earlier.
export async function getQuarters(symbol: string): Promise<Quarter[]> {
  return sql<Quarter[]>`
    select period, period_end::text as period_end, sales::float8 as sales,
           operating_profit::float8 as operating_profit, opm::float8 as opm,
           pbt::float8 as pbt, net_profit::float8 as net_profit
    from quarterly_results where symbol = ${symbol}
    order by period_end desc limit 5`;
}

export async function getLatestResults(symbol: string): Promise<LatestResults | null> {
  const [row] = await sql<LatestResults[]>`
    select published_at, pdf_url, extracted from announcements
    where symbol = ${symbol} and category = 'results'
      and jsonb_typeof(extracted->'metrics') = 'object'
    order by published_at desc limit 1`;
  return row ?? null;
}

export async function getPromises(symbol: string): Promise<PromiseRow[]> {
  return sql<PromiseRow[]>`
    select p.id, p.symbol, p.metric, p.target_value, p.target_period, p.quote,
           p.said_on::text as said_on, p.basis, p.period_type, p.status, p.actual_value,
           p.comparison, p.scoring_note, d.period as source_period, d.url as source_url
    from promises p left join documents d on d.id = p.source_doc
    where p.symbol = ${symbol}
    order by p.said_on desc nulls last, p.target_period, p.metric`;
}

export async function getAnalysis(symbol: string): Promise<Analysis | null> {
  const [row] = await sql<Analysis[]>`
    select symbol, payload, model_used, degraded, built_at from analysis where symbol = ${symbol}`;
  return row ?? null;
}

export type CompanyListing = Company & { close: number | null; pct_change: number | null };

export async function getCompanyList(): Promise<CompanyListing[]> {
  return sql<CompanyListing[]>`
    select c.symbol, c.name, c.sector, c.in_portfolio,
           p.close::float8 as close, p.pct_change::float8 as pct_change
    from companies c
    left join lateral (
      select close, pct_change from prices where symbol = c.symbol order by date desc limit 1
    ) p on true
    order by c.in_portfolio desc, c.symbol`;
}

// Alert rules and the feed they trip, evaluated at read time over the stored derived columns.
import "server-only";
import { sql } from "./db";

export type RuleKind = "price_move" | "volume_spike" | "category";

export type Rule = {
  kind: RuleKind;
  threshold: number | null;
  categories: string[];
  enabled: boolean;
};

export type Fired = {
  kind: RuleKind;
  symbol: string;
  at: Date;
  value: number;
  threshold: number | null;
  title: string | null;
  pdf_url: string | null;
  category: string | null;
};

export const FEED_DAYS = 30;

export async function getRules(): Promise<Record<RuleKind, Rule>> {
  const rows = await sql<Rule[]>`
    select kind, threshold::float8 as threshold, coalesce(categories, '{}') as categories, enabled
    from alert_rules`;
  const byKind = Object.fromEntries(rows.map((r) => [r.kind, r])) as Partial<Record<RuleKind, Rule>>;
  const fallback = (kind: RuleKind): Rule => ({ kind, threshold: null, categories: [], enabled: false });
  return {
    price_move: byKind.price_move ?? fallback("price_move"),
    volume_spike: byKind.volume_spike ?? fallback("volume_spike"),
    category: byKind.category ?? fallback("category"),
  };
}

export async function getAnchors(): Promise<{ filings: Date | null; prices: string | null }> {
  const [row] = await sql<{ filings: Date | null; prices: string | null }[]>`
    select (select max(published_at) from announcements) as filings,
           (select max(date)::text from prices) as prices`;
  return row;
}

export async function getFeed(): Promise<Fired[]> {
  return sql<Fired[]>`
    with pa as (select max(date) as d from prices),
         aa as (select max(published_at) as t from announcements),
         r  as (select kind, threshold::float8 as threshold, categories
                from alert_rules where enabled)
    select 'price_move' as kind, p.symbol,
           -- a daily bar is known at the close, 15:30 IST
           (p.date + time '15:30') at time zone 'Asia/Kolkata' as at,
           p.pct_change::float8 as value, r.threshold,
           null as title, null as pdf_url, null as category
    from prices p
    join companies c on c.symbol = p.symbol and c.in_portfolio
    cross join pa
    join r on r.kind = 'price_move'
    where p.date > pa.d - ${FEED_DAYS}::int and abs(p.pct_change) >= r.threshold
    union all
    select 'volume_spike', p.symbol,
           (p.date + time '15:30') at time zone 'Asia/Kolkata',
           p.vol_ratio::float8, r.threshold, null, null, null
    from prices p
    join companies c on c.symbol = p.symbol and c.in_portfolio
    cross join pa
    join r on r.kind = 'volume_spike'
    where p.date > pa.d - ${FEED_DAYS}::int and p.vol_ratio >= r.threshold
    union all
    select 'category', a.symbol, a.published_at,
           a.materiality::float8, null, a.title, a.pdf_url, a.category
    from announcements a
    join companies c on c.symbol = a.symbol and c.in_portfolio
    cross join aa
    join r on r.kind = 'category'
    where a.published_at > aa.t - make_interval(days => ${FEED_DAYS}::int)
      and a.category = any(r.categories)
    order by at desc
    limit 200`;
}

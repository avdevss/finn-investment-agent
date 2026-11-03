// Digest query: filings in a window ending at the latest filing, grouped and tiered.
import "server-only";
import { sql } from "./db";
import { CARD_THRESHOLD, ROW_THRESHOLD } from "./categories";
import type { DigestItem } from "./types";

export const WINDOWS = { "24h": "24 hours", "3d": "3 days", "7d": "7 days", "30d": "30 days" } as const;
export type WindowKey = keyof typeof WINDOWS;

export function parseWindow(raw: string | string[] | undefined): WindowKey {
  return typeof raw === "string" && raw in WINDOWS ? (raw as WindowKey) : "24h";
}

export type Digest = {
  anchor: Date | null;
  cards: DigestItem[];
  rows: DigestItem[];
  routine: DigestItem[];
  filings: number;
};

export async function getDigest(window: WindowKey): Promise<Digest> {
  const interval = WINDOWS[window];
  const items = await sql<(DigestItem & { anchor: Date })[]>`
    with anchor as (select max(published_at) as t from announcements),
    win as (
      select a.id, a.symbol, c.name, a.published_at, a.title, a.category, a.pdf_url,
             a.materiality, a.score_parts, a.summary, a.extracted, anchor.t as anchor
      from announcements a
      join companies c on c.symbol = a.symbol
      cross join anchor
      where c.in_portfolio
        and a.published_at >  anchor.t - ${interval}::interval
        and a.published_at <= anchor.t
    ),
    ranked as (
      select win.*,
             row_number() over g as rk,
             (count(*) over (partition by symbol, category) - 1)::int as related_count
      from win
      window g as (partition by symbol, category order by materiality desc, published_at desc)
    ),
    related as (
      select symbol, category,
             json_agg(json_build_object('id', id, 'title', title, 'pdf_url', pdf_url,
                      'published_at', published_at, 'materiality', materiality)
                      order by published_at desc) as items
      from ranked where rk > 1
      group by symbol, category
    )
    select l.*,
           coalesce(r.items, '[]'::json) as related,
           coalesce(d.promises, '[]'::json) as due_promises
    from ranked l
    left join related r using (symbol, category)
    left join lateral (
      select json_agg(p order by p.metric) as promises
      from promises p
      where l.category = 'results'
        and p.symbol = l.symbol
        and l.extracted->>'period' is not null
        and ( p.target_period = l.extracted->>'period'
              -- a Q4 statement also closes the fiscal year it ends
           or (l.extracted->>'period' like 'Q4FY%'
               and p.target_period = substr(l.extracted->>'period', 3)) )
    ) d on true
    where l.rk = 1
    order by l.materiality desc, l.published_at desc
  `;

  const anchor = items[0]?.anchor ?? (await latestFiling());
  return {
    anchor,
    cards: items.filter((i) => i.materiality >= CARD_THRESHOLD),
    rows: items.filter((i) => i.materiality >= ROW_THRESHOLD && i.materiality < CARD_THRESHOLD),
    routine: items.filter((i) => i.materiality < ROW_THRESHOLD),
    filings: items.reduce((n, i) => n + 1 + i.related_count, 0),
  };
}

export async function latestFiling(): Promise<Date | null> {
  const [row] = await sql<{ t: Date | null }[]>`select max(published_at) as t from announcements`;
  return row.t;
}

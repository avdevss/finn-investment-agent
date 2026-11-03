// Database side of a live run: the daily cap and the idempotent writes that make the next
// request for the same ticker a cache hit.
import "server-only";
import { sql } from "@/lib/db";
import type { AnalysisPayload } from "@/lib/types";
import type { QuarterRow } from "./screener";

export const LIVE_RUNS_PER_DAY = 10;

// Whether the batch ingest already owns this company, and whether an analysis row exists.
export async function knownSymbol(symbol: string): Promise<{ ingested: boolean; analysed: boolean }> {
  const [row] = await sql<{ ingested: boolean; analysed: boolean }[]>`
    select exists(select 1 from companies where symbol = ${symbol}) as ingested,
           exists(select 1 from analysis where symbol = ${symbol}) as analysed`;
  return row;
}

// Successful live runs since midnight IST.
export async function liveRunsToday(): Promise<number> {
  const [row] = await sql<{ n: number }[]>`
    select count(*)::int as n from analysis
    where payload->>'source' = 'live'
      and (built_at at time zone 'Asia/Kolkata')::date = (now() at time zone 'Asia/Kolkata')::date`;
  return row.n;
}

export async function saveLiveRun(run: {
  symbol: string;
  company: { name: string; sector: string | null };
  quarters: QuarterRow[];
  payload: AnalysisPayload;
  model: string;
  promptTokens: number;
  outputTokens: number;
}): Promise<void> {
  // Concurrent runs on one ticker both land here; every write is an upsert, so the last wins cleanly.
  await sql.begin(async (tx) => {
    await tx`
      insert into companies (symbol, name, sector, in_portfolio, screener_slug)
      values (${run.symbol}, ${run.company.name}, ${run.company.sector}, false, ${run.symbol})
      on conflict (symbol) do update set name = excluded.name,
        sector = coalesce(companies.sector, excluded.sector)`;
    for (const q of run.quarters) {
      await tx`
        insert into quarterly_results (symbol, period_end, period, sales, operating_profit, opm,
          other_income, interest, depreciation, pbt, net_profit, source)
        values (${run.symbol}, ${q.period_end}, ${q.period}, ${q.sales}, ${q.operating_profit}, ${q.opm},
          ${q.other_income}, ${q.interest}, ${q.depreciation}, ${q.pbt}, ${q.net_profit}, 'screener_quarters')
        on conflict (symbol, period_end) do update set period = excluded.period,
          sales = excluded.sales, operating_profit = excluded.operating_profit, opm = excluded.opm,
          other_income = excluded.other_income, interest = excluded.interest,
          depreciation = excluded.depreciation, pbt = excluded.pbt, net_profit = excluded.net_profit`;
    }
    await tx`
      insert into analysis (symbol, payload, model_used, degraded, prompt_tokens, output_tokens, built_at)
      values (${run.symbol}, ${tx.json(run.payload as never)}, ${run.model}, true,
              ${run.promptTokens}, ${run.outputTokens}, now())
      on conflict (symbol) do update set payload = excluded.payload, model_used = excluded.model_used,
        degraded = excluded.degraded, prompt_tokens = excluded.prompt_tokens,
        output_tokens = excluded.output_tokens, built_at = excluded.built_at`;
  });
}

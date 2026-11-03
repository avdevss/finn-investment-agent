// Research: any NSE symbol. Cached analysis renders at once; anything else runs the live pipeline.
import Link from "next/link";
import AnalysisPanel from "@/components/AnalysisPanel";
import PromiseLedger from "@/components/PromiseLedger";
import ResearchProgress from "@/components/ResearchProgress";
import { getAnalysis, getCompany, getPromises } from "@/lib/company";
import { getCachedSymbols, parseSymbol } from "@/lib/research";
import { LIVE_RUNS_PER_DAY, liveRunsToday } from "@/lib/live/store";

export const dynamic = "force-dynamic";

export default async function ResearchPage({ searchParams }: {
  searchParams: Promise<{ q?: string | string[] }>;
}) {
  const raw = (await searchParams).q;
  const symbol = parseSymbol(raw);
  const [cached, company, analysis, promises, runsToday] = await Promise.all([
    getCachedSymbols(),
    symbol ? getCompany(symbol) : null,
    symbol ? getAnalysis(symbol) : null,
    symbol ? getPromises(symbol) : [],
    liveRunsToday(),
  ]);

  return (
    <>
      <h1 className="mb-2 text-base font-semibold text-n-50">Research</h1>
      <form action="/research" className="flex border border-n-700">
        <input
          name="q" defaultValue={symbol ?? (typeof raw === "string" ? raw : "")}
          placeholder="NSE symbol, e.g. KPITTECH" autoCapitalize="characters" autoComplete="off"
          spellCheck={false}
          className="min-w-0 flex-1 bg-transparent px-3 py-2 text-base text-n-50 uppercase placeholder:normal-case placeholder:text-n-500 focus:outline-none"
        />
        <button type="submit" className="border-l border-n-700 px-4 text-xs font-medium uppercase tracking-wide text-accent">
          Run
        </button>
      </form>
      {raw !== undefined && !symbol && (
        <p className="mt-2 text-xs text-n-400">Enter an NSE symbol: letters, digits, & or -.</p>
      )}

      {symbol && analysis ? (
        <section className="mt-4">
          <div className="mb-1 flex items-baseline justify-between">
            <h2 className="font-semibold text-n-50">{symbol}</h2>
            <Link href={`/company/${symbol}`} className="text-xs text-accent">Full company page</Link>
          </div>
          {company && (
            <p className="mb-2 text-xs text-n-500">
              {company.name} · {analysis.payload.source === "live" ? "built live" : "cached from ingest"}
            </p>
          )}
          <AnalysisPanel analysis={analysis} />
          <h3 className="mt-4 mb-1 text-xs font-semibold uppercase tracking-wide text-n-400">Promise Ledger</h3>
          <PromiseLedger promises={promises} />
        </section>
      ) : symbol && company ? (
        <p className="mt-4 border-y border-n-800 py-3 text-n-400">
          {symbol} is covered by the batch ingest; its analysis has not been built yet.{" "}
          <Link href={`/company/${symbol}`} className="text-accent">Company page</Link>
        </p>
      ) : symbol ? (
        <ResearchProgress key={symbol} symbol={symbol} runsLeft={Math.max(0, LIVE_RUNS_PER_DAY - runsToday)} />
      ) : null}

      <section className="mt-6">
        <h2 className="num mb-1 text-xs uppercase tracking-wide text-n-500">Cached · {cached.length}</h2>
        <div className="flex flex-wrap gap-1.5">
          {cached.map((c) => (
            <Link key={c.symbol} href={`/research?q=${c.symbol}`}
              className={`border px-2 py-1 text-xs ${c.in_portfolio ? "border-n-700 text-n-400" : "border-n-500 text-n-200"}`}>
              {c.symbol}
            </Link>
          ))}
        </div>
      </section>
    </>
  );
}

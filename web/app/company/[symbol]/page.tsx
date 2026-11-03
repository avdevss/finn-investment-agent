// Company: price and volume, headline numbers, the Promise Ledger and the analysis.
import { notFound } from "next/navigation";
import AnalysisPanel from "@/components/AnalysisPanel";
import { PdfLink } from "@/components/FilingCard";
import PriceChart from "@/components/PriceChart";
import PromiseLedger from "@/components/PromiseLedger";
import QuarterTable from "@/components/QuarterTable";
import ResultsFigures from "@/components/ResultsFigures";
import {
  getAnalysis, getCompany, getLatestResults, getPrices, getPromises, getQuarters,
} from "@/lib/company";
import { istDate, num, signedPct } from "@/lib/format";
import { parseSymbol } from "@/lib/research";

export const dynamic = "force-dynamic";

export default async function CompanyPage({ params }: { params: Promise<{ symbol: string }> }) {
  const symbol = parseSymbol(decodeURIComponent((await params).symbol));
  if (!symbol) notFound();
  const [company, prices, quarters, latest, promises, analysis] = await Promise.all([
    getCompany(symbol), getPrices(symbol), getQuarters(symbol),
    getLatestResults(symbol), getPromises(symbol), getAnalysis(symbol),
  ]);
  if (!company) notFound();
  const last = prices.at(-1);

  return (
    <>
      <header className="mb-2">
        <div className="flex items-baseline justify-between gap-2">
          <h1 className="text-base font-semibold text-n-50">{company.symbol}</h1>
          {last && (
            <span className="num text-base text-n-50">
              ₹{num(last.close)} <span className="text-sm text-n-400">{signedPct(last.pct_change, 2)}</span>
            </span>
          )}
        </div>
        <p className="text-xs text-n-500">
          {company.name}{company.sector ? ` · ${company.sector}` : ""}
          {last ? ` · close ${istDate(last.date)}` : ""}
          {company.in_portfolio ? "" : " · research, not in portfolio"}
        </p>
      </header>

      <PriceChart data={prices} />

      <Block title="Headline numbers">
        <QuarterTable quarters={quarters} />
        {latest && (
          <div className="mt-3">
            <PdfLink url={latest.pdf_url} className="text-xs text-n-400 underline decoration-n-700">
              Latest results filing, {istDate(latest.published_at)}
            </PdfLink>
            <ResultsFigures extracted={latest.extracted} />
          </div>
        )}
      </Block>

      <Block title="Promise Ledger">
        <PromiseLedger promises={promises} />
      </Block>

      <Block title="Analysis">
        <AnalysisPanel analysis={analysis} />
      </Block>
    </>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-5">
      <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-n-400">{title}</h2>
      {children}
    </section>
  );
}

// Alerts: the investor's thresholds, and every filing or price move that trips them.
import Link from "next/link";
import AlertRulesForm from "@/components/AlertRulesForm";
import { PdfLink } from "@/components/FilingCard";
import { DataAsOf } from "@/components/WindowPicker";
import { FEED_DAYS, getAnchors, getFeed, getRules, type Fired } from "@/lib/alerts";
import { categoryLabel } from "@/lib/categories";
import { istDate, istDateTime, signedPct } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function AlertsPage() {
  const [rules, feed, anchors] = await Promise.all([getRules(), getFeed(), getAnchors()]);
  return (
    <>
      <h1 className="mb-1 text-base font-semibold text-n-50">Alerts</h1>
      <DataAsOf anchor={anchors.filings} extra={anchors.prices ? `prices to ${istDate(anchors.prices)}` : undefined} />
      <section className="mt-3">
        <AlertRulesForm price={rules.price_move} volume={rules.volume_spike} category={rules.category} />
      </section>
      <section className="mt-4">
        <h2 className="num mb-1 text-xs uppercase tracking-wide text-n-500">
          Fired · last {FEED_DAYS} days · {feed.length}{feed.length === 200 ? "+" : ""}
        </h2>
        {feed.length === 0 ? (
          <p className="py-4 text-n-400">Nothing tripped the current rules. Loosen a threshold above.</p>
        ) : (
          <ul>{feed.map((f, i) => <FiredRow key={`${f.kind}-${f.symbol}-${i}`} fired={f} />)}</ul>
        )}
      </section>
    </>
  );
}

function FiredRow({ fired: f }: { fired: Fired }) {
  return (
    <li className="flex items-start gap-3 border-b border-n-800 py-2">
      <div className="min-w-0 flex-1">
        <div className="text-xs">
          <Link href={`/company/${f.symbol}`} className="font-semibold text-n-50">{f.symbol}</Link>
          <span className="num ml-2 text-n-500">
            {f.kind === "category" ? istDateTime(f.at) : istDate(f.at)}
          </span>
        </div>
        <p className="mt-0.5 text-n-200">{reason(f)}</p>
        {f.kind === "category" && (
          <PdfLink url={f.pdf_url} className="mt-0.5 block truncate text-xs text-n-500">{f.title}</PdfLink>
        )}
      </div>
      <span className="num shrink-0 text-sm text-n-50">{magnitude(f)}</span>
    </li>
  );
}

function reason(f: Fired): string {
  if (f.kind === "price_move") {
    return `Price move rule: ${signedPct(f.value, 1)} vs ±${f.threshold?.toFixed(1)}%`;
  }
  if (f.kind === "volume_spike") {
    return `Volume rule: ${f.value.toFixed(1)}× vs ${f.threshold?.toFixed(1)}× 20 day average`;
  }
  return `Category rule: ${categoryLabel(f.category ?? "")} filed, materiality ${f.value}`;
}

function magnitude(f: Fired): string {
  if (f.kind === "price_move") return signedPct(f.value, 1);
  if (f.kind === "volume_spike") return `${f.value.toFixed(1)}×`;
  return String(f.value);
}

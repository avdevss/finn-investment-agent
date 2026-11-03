// Material filing, full card: summary links to the source PDF, score opens its breakdown.
import Link from "next/link";
import { categoryLabel } from "@/lib/categories";
import { istDateTime } from "@/lib/format";
import type { DigestItem } from "@/lib/types";
import PromiseItem from "./PromiseItem";
import ResultsFigures from "./ResultsFigures";
import ScoreChip from "./ScoreChip";

export default function FilingCard({ item }: { item: DigestItem }) {
  return (
    <article className="border-b border-n-800 py-3">
      <header className="flex items-start gap-2">
        <div className="min-w-0 flex-1 text-xs">
          <Link href={`/company/${item.symbol}`} className="font-semibold text-n-50">
            {item.symbol}
          </Link>
          <span className="ml-2 border border-n-700 px-1 py-px uppercase tracking-wide text-n-400">
            {categoryLabel(item.category)}
          </span>
          <span className="num ml-2 text-n-500">{istDateTime(item.published_at)}</span>
        </div>
        <ScoreChip score={item.materiality} parts={item.score_parts} />
      </header>

      <PdfLink url={item.pdf_url} className="mt-1.5 block">
        <p className="text-base text-n-50">{item.summary ?? item.title}</p>
        {item.summary && <p className="mt-0.5 text-xs text-n-500">{item.title}</p>}
      </PdfLink>

      {item.category === "results" && item.extracted && (
        <ResultsFigures extracted={item.extracted} />
      )}

      {item.due_promises.length > 0 && (
        <section className="mt-2">
          <h3 className="text-xs uppercase tracking-wide text-n-500">Promises due this period</h3>
          {item.due_promises.map((p) => <PromiseItem key={p.id} promise={p} />)}
        </section>
      )}

      {item.related_count > 0 && <Related item={item} />}
    </article>
  );
}

export function PdfLink({ url, className, children }: {
  url: string | null; className?: string; children: React.ReactNode;
}) {
  if (!url) return <div className={className}>{children}</div>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className={className}>
      {children}
    </a>
  );
}

function Related({ item }: { item: DigestItem }) {
  return (
    <details className="mt-2 text-xs">
      <summary className="text-n-400">+{item.related_count} related</summary>
      <ul className="mt-1">
        {item.related.map((r) => (
          <li key={r.id} className="flex gap-2 border-t border-n-800 py-1">
            <span className="num shrink-0 text-n-500">{istDateTime(r.published_at)}</span>
            <PdfLink url={r.pdf_url} className="min-w-0 flex-1 truncate text-n-400">{r.title}</PdfLink>
            <span className="num shrink-0 text-n-500">{r.materiality}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

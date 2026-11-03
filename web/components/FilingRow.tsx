// Mid tier filing, one compact row.
import Link from "next/link";
import { categoryLabel } from "@/lib/categories";
import { istDateTime } from "@/lib/format";
import type { DigestItem } from "@/lib/types";
import { PdfLink } from "./FilingCard";
import ScoreChip from "./ScoreChip";

export default function FilingRow({ item }: { item: DigestItem }) {
  return (
    <li className="flex items-start gap-2 border-b border-n-800 py-2">
      <div className="min-w-0 flex-1">
        <div className="text-xs">
          <Link href={`/company/${item.symbol}`} className="font-semibold text-n-200">{item.symbol}</Link>
          <span className="ml-2 text-n-500">{categoryLabel(item.category)}</span>
          <span className="num ml-2 text-n-500">{istDateTime(item.published_at)}</span>
          {item.related_count > 0 && <span className="num ml-2 text-n-500">+{item.related_count}</span>}
        </div>
        <PdfLink url={item.pdf_url} className="mt-0.5 block truncate text-n-200">
          {item.summary ?? item.title}
        </PdfLink>
      </div>
      <ScoreChip score={item.materiality} parts={item.score_parts} />
    </li>
  );
}

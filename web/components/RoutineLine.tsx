// Routine filings, collapsed into one grey line that expands in place.
import type { DigestItem } from "@/lib/types";
import FilingRow from "./FilingRow";

export default function RoutineLine({ items, count }: { items: DigestItem[]; count: number }) {
  if (items.length === 0) return null;
  return (
    <details className="mt-3 text-n-500">
      <summary className="num border-y border-n-800 py-2 text-xs">
        {count} routine filing{count === 1 ? "" : "s"} · tap to expand
      </summary>
      <ul className="opacity-80">
        {items.map((item) => <FilingRow key={item.id} item={item} />)}
      </ul>
    </details>
  );
}

// Segmented time window control plus the "data as of" line that anchors it.
import Link from "next/link";
import { istDateTime } from "@/lib/format";

export default function WindowPicker<K extends string>({ options, active, basePath, anchor, extra }: {
  options: readonly K[]; active: K; basePath: string; anchor: Date | null; extra?: string;
}) {
  return (
    <div>
      <div className="grid grid-cols-4 border border-n-700">
        {options.map((key) => (
          <Link
            key={key}
            href={`${basePath}?w=${key}`}
            className={`py-1.5 text-center text-xs font-medium ${
              key === active ? "bg-n-800 text-accent" : "text-n-400"
            }`}
          >
            {key}
          </Link>
        ))}
      </div>
      <DataAsOf anchor={anchor} extra={extra} />
    </div>
  );
}

export function DataAsOf({ anchor, extra }: { anchor: Date | null; extra?: string }) {
  return (
    <p className="num mt-1.5 text-xs text-n-500">
      Data as of {anchor ? `${istDateTime(anchor)} IST` : "—"}
      {extra ? ` · ${extra}` : ""}
    </p>
  );
}

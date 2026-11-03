// Headline quarterly numbers with QoQ and YoY, from the screener quarterly table.
import { num, pctChange, signedPct } from "@/lib/format";
import type { Quarter } from "@/lib/company";

const ROWS: { key: keyof Quarter; label: string }[] = [
  { key: "sales", label: "Revenue" },
  { key: "operating_profit", label: "EBITDA" },
  { key: "pbt", label: "PBT" },
  { key: "net_profit", label: "PAT" },
];

export default function QuarterTable({ quarters }: { quarters: Quarter[] }) {
  const [current, prior] = quarters;
  if (!current) return <p className="py-3 text-n-500">No quarterly results stored.</p>;
  const yearAgo = quarters.find((q) => q.period_end === shiftYear(current.period_end)) ?? null;

  return (
    <div>
      <table className="w-full">
        <thead className="text-xs text-n-500">
          <tr>
            <th className="py-0.5 text-left font-normal">{current.period} · ₹ cr</th>
            <th className="py-0.5 text-right font-normal">Value</th>
            <th className="py-0.5 text-right font-normal">QoQ</th>
            <th className="py-0.5 text-right font-normal">YoY</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map(({ key, label }) => {
            const value = current[key] as number | null;
            return (
              <tr key={key} className="border-t border-n-800">
                <td className="py-1 text-n-400">{label}</td>
                <td className="num py-1 text-right text-n-50">{num(value)}</td>
                <td className="num py-1 text-right">{signedPct(pctChange(value, (prior?.[key] as number) ?? null))}</td>
                <td className="num py-1 text-right">{signedPct(pctChange(value, (yearAgo?.[key] as number) ?? null))}</td>
              </tr>
            );
          })}
          <tr className="border-t border-n-800">
            <td className="py-1 text-n-400">EBITDA margin</td>
            <td className="num py-1 text-right text-n-50">{current.opm === null ? "—" : `${current.opm}%`}</td>
            <td className="num py-1 text-right">{pp(current.opm, prior?.opm ?? null)}</td>
            <td className="num py-1 text-right">{pp(current.opm, yearAgo?.opm ?? null)}</td>
          </tr>
        </tbody>
      </table>
      <p className="mt-1 text-xs text-n-500">
        Screener quarterly table · EBITDA is screener operating profit
        {yearAgo ? "" : " · year ago quarter not stored"}
      </p>
    </div>
  );
}

// Margins move in percentage points, not percent.
function pp(current: number | null, base: number | null): string {
  if (current === null || base === null) return "—";
  const d = current - base;
  return `${d > 0 ? "+" : d < 0 ? "−" : ""}${Math.abs(d).toFixed(1)}pp`;
}

function shiftYear(isoDate: string): string {
  const [y, m, d] = isoDate.split("-");
  return `${Number(y) - 1}-${m}-${d}`;
}

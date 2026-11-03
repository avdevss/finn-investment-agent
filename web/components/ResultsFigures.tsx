// Headline numbers from a results filing, current quarter with QoQ and YoY, and their source.
import { num, signedPct } from "@/lib/format";
import type { Extracted, Figure } from "@/lib/types";

const ROWS = [
  ["revenue", "Revenue"],
  ["ebitda", "EBITDA"],
  ["pbt", "PBT"],
  ["pat", "PAT"],
] as const;

export default function ResultsFigures({ extracted }: { extracted: Extracted }) {
  if (!extracted.metrics) {
    return (
      <p className="mt-2 text-xs text-n-500">
        Figures not read: {extracted.reason ?? "no statement parsed from the filing"}.
      </p>
    );
  }
  const metrics = extracted.metrics;
  return (
    <div className="mt-2">
      <table className="w-full text-sm">
        <thead className="text-xs text-n-500">
          <tr>
            <th className="py-0.5 text-left font-normal">{extracted.period}</th>
            <th className="py-0.5 text-right font-normal">Value</th>
            <th className="py-0.5 text-right font-normal">QoQ</th>
            <th className="py-0.5 text-right font-normal">YoY</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map(([key, label]) => (
            <Row key={key} label={label} figure={metrics[key] ?? null} />
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-xs text-n-500">
        {extracted.basis} · page {extracted.page} · {extracted.units ?? "units as filed"} ·
        EBITDA = {extracted.ebitda_basis}
      </p>
    </div>
  );
}

function Row({ label, figure }: { label: string; figure: Figure | null }) {
  return (
    <tr className="border-t border-n-800">
      <td className="py-0.5 text-n-400" title={figure?.label}>{label}</td>
      <td className="num py-0.5 text-right text-n-50">{num(figure?.current)}</td>
      <td className="num py-0.5 text-right">{signedPct(figure?.qoq_pct)}</td>
      <td className="num py-0.5 text-right">{signedPct(figure?.yoy_pct)}</td>
    </tr>
  );
}

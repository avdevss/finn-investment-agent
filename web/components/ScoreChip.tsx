// Materiality score that opens its own arithmetic when tapped.
import { categoryLabel } from "@/lib/categories";
import { signedPct } from "@/lib/format";
import type { ScoreParts } from "@/lib/types";

export default function ScoreChip({ score, parts }: { score: number; parts: ScoreParts }) {
  return (
    <details className="relative shrink-0">
      <summary
        aria-label={`Materiality ${score}, show breakdown`}
        className="num min-w-8 border border-accent/60 px-1.5 py-0.5 text-center text-xs font-semibold text-accent"
      >
        {score}
      </summary>
      <div className="absolute right-0 top-full z-20 mt-1 w-64 border border-n-700 bg-n-900 p-3 text-xs text-n-400">
        <table className="w-full">
          <tbody>
            <Line label={`Category · ${categoryLabel(parts.category)}`} value={parts.category_weight} />
            <Line
              label={`Price · |${signedPct(parts.pct_change, 2)}| × 4, cap 25`}
              value={parts.price_component}
            />
            <Line
              label={`Volume · (${parts.vol_ratio?.toFixed(2) ?? "—"}× − 1) × 10, cap 15`}
              value={parts.volume_component}
            />
            <tr className="border-t border-n-700 text-n-50">
              <td className="pt-1">Total, cap 100</td>
              <td className="pt-1 text-right font-semibold">{parts.total}</td>
            </tr>
          </tbody>
        </table>
        {parts.notice_cap && (
          <p className="mt-2">
            Category weight capped from {parts.notice_cap.uncapped}: {parts.notice_cap.reason}.
          </p>
        )}
        <p className="mt-2">
          Price day {parts.price_date ?? "none"}
          {parts.price_basis ? ` · ${parts.price_basis}` : ""}
        </p>
      </div>
    </details>
  );
}

function Line({ label, value }: { label: string; value: number }) {
  return (
    <tr>
      <td className="py-0.5 pr-2">{label}</td>
      <td className="py-0.5 text-right text-n-200">{value}</td>
    </tr>
  );
}

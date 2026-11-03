// One Promise Ledger entry: target, verdict, why it is still open, and the verbatim quote.
import { callMonth, callMonthsIn } from "@/lib/format";
import type { PromiseRow } from "@/lib/types";

const METRIC: Record<string, string> = {
  revenue_growth: "Revenue growth",
  ebitda_margin: "EBITDA margin",
  capex: "Capex",
  other: "Other",
};

const VERDICT: Record<PromiseRow["status"], string> = {
  kept: "font-semibold text-n-50",
  missed: "font-semibold text-n-50 line-through decoration-n-400",
  open: "text-n-400",
  dropped: "text-n-500",
};

export default function PromiseItem({ promise }: { promise: PromiseRow }) {
  const p = promise;
  return (
    <details className="border-t border-n-800 py-2">
      <summary className="grid grid-cols-[1fr_auto] gap-x-3">
        <span>
          <span className="text-n-400">{METRIC[p.metric] ?? p.metric}</span>{" "}
          <span className="num text-n-50">{p.target_value ?? "—"}</span>{" "}
          <span className="num text-n-400">by {p.target_period ?? "—"}</span>
        </span>
        <span className={`text-xs uppercase tracking-wide ${VERDICT[p.status]}`}>{p.status}</span>
        {p.actual_value && (
          <span className="num col-span-2 text-xs text-n-400">Actual {p.actual_value}</span>
        )}
        {p.status === "open" && p.scoring_note && (
          <span className="col-span-2 text-xs text-n-500">Open: {callMonthsIn(p.scoring_note)}</span>
        )}
      </summary>
      <blockquote className="mt-2 border-l border-accent/60 pl-3 text-sm text-n-200">
        “{p.quote}”
      </blockquote>
      <p className="num mt-1 pl-3 text-xs text-n-500">
        {p.said_on ? `Said ${callMonth(p.said_on)}` : "Date unknown"}
        {p.basis ? ` · basis ${p.basis}` : ""}
        {p.period_type ? ` · ${p.period_type}` : ""}
        {p.comparison ? ` · read as ${p.comparison}` : ""}
        {p.source_url && (
          <>
            {" · "}
            <a href={p.source_url} target="_blank" rel="noreferrer" className="underline decoration-n-700">
              {p.source_period ?? "transcript"} transcript
            </a>
          </>
        )}
      </p>
    </details>
  );
}

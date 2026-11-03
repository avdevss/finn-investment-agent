// Business snapshot and bull versus bear, collapsible, with the model that wrote it.
import { istDate } from "@/lib/format";
import type { Analysis } from "@/lib/types";

export default function AnalysisPanel({ analysis }: { analysis: Analysis | null }) {
  if (!analysis) {
    return <p className="py-3 text-n-500">No analysis built for this company yet.</p>;
  }
  const p = analysis.payload;
  return (
    <div>
      <Section title="Business snapshot" open>
        <p>{p.snapshot}</p>
        <p className="mt-2 text-n-400">{p.how_it_makes_money}</p>
        {p.segments && p.segments.length > 0 && (
          <ul className="mt-2">
            {p.segments.map((s) => (
              <li key={s.name} className="border-t border-n-800 py-1">
                <span className="text-n-50">{s.name}</span> <span className="text-n-400">{s.note}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Financial read">
        <p>{p.financial_read}</p>
      </Section>
      <Section title="Bull">
        <Points points={p.bull} />
      </Section>
      <Section title="Bear">
        <Points points={p.bear} />
      </Section>
      {p.guidance && <Guidance payload={p} />}
      <p className="mt-2 text-xs text-n-500">
        {p.source === "live" ? "Built live · " : ""}
        {analysis.model_used ?? "model unknown"}
        {analysis.degraded ? " · cheap tier" : ""} · built {istDate(analysis.built_at)}
      </p>
    </div>
  );
}

function Section({ title, open, children }: { title: string; open?: boolean; children: React.ReactNode }) {
  return (
    <details open={open} className="border-t border-n-800 py-2">
      <summary className="text-xs uppercase tracking-wide text-n-400">{title}</summary>
      <div className="mt-1.5 text-n-200">{children}</div>
    </details>
  );
}

function Guidance({ payload: p }: { payload: Analysis["payload"] }) {
  const quotes = p.guidance ?? [];
  return (
    <Section title={`Guidance · ${quotes.length}`} open>
      <p className="text-xs text-n-500">
        Verbatim from the {p.transcript_period ?? "latest"}{" "}
        {p.transcript_url ? (
          <a href={p.transcript_url} target="_blank" rel="noreferrer" className="underline decoration-n-700">transcript</a>
        ) : "transcript"}
        ; management attribution not verified
        {p.guidance_rejected ? `; ${p.guidance_rejected} model quotes dropped as not verbatim` : ""}.
      </p>
      {quotes.length === 0 ? (
        <p className="mt-1.5 text-n-500">No forward looking statement survived the verbatim check.</p>
      ) : (
        <ul className="mt-1.5">
          {quotes.map((g) => (
            <li key={g.quote} className="border-t border-n-800 py-1.5">
              <span className="text-xs uppercase tracking-wide text-n-500">{g.topic}</span>
              <blockquote className="mt-0.5 border-l border-accent/60 pl-3">“{g.quote}”</blockquote>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function Points({ points }: { points: string[] }) {
  return (
    <ol className="space-y-1.5">
      {points.map((point, i) => (
        <li key={i} className="grid grid-cols-[16px_1fr]">
          <span className="num text-n-500">{i + 1}</span>
          <span>{point}</span>
        </li>
      ))}
    </ol>
  );
}

// The Promise Ledger grouped by verdict, headed by management's track record.
import type { PromiseRow } from "@/lib/types";
import PromiseItem from "./PromiseItem";

const ORDER: PromiseRow["status"][] = ["kept", "missed", "open", "dropped"];

export default function PromiseLedger({ promises }: { promises: PromiseRow[] }) {
  if (promises.length === 0) {
    return <p className="py-3 text-n-500">No quantified commitments extracted from the transcripts.</p>;
  }
  const groups = ORDER.map((status) => ({
    status, items: promises.filter((p) => p.status === status),
  }));
  const scored = groups[0].items.length + groups[1].items.length;

  return (
    <div>
      <p className="num text-xs text-n-400">
        {groups.map((g) => `${g.items.length} ${g.status}`).join(" · ")}
        {scored > 0 && ` · ${Math.round((groups[0].items.length / scored) * 100)}% of scored promises kept`}
      </p>
      {groups.filter((g) => g.items.length > 0).map((g) => (
        <section key={g.status} className="mt-2">
          <h3 className="num text-xs uppercase tracking-wide text-n-500">{g.status} · {g.items.length}</h3>
          {g.items.map((p) => <PromiseItem key={p.id} promise={p} />)}
        </section>
      ))}
    </div>
  );
}

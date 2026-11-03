// Digest: portfolio filings in a window, sorted by materiality, in three tiers.
import FilingCard from "@/components/FilingCard";
import FilingRow from "@/components/FilingRow";
import RoutineLine from "@/components/RoutineLine";
import WindowPicker from "@/components/WindowPicker";
import { getDigest, parseWindow, WINDOWS, type WindowKey } from "@/lib/digest";

export const dynamic = "force-dynamic";

const WIDER: Record<WindowKey, WindowKey | null> = { "24h": "3d", "3d": "7d", "7d": "30d", "30d": null };

export default async function DigestPage({ searchParams }: {
  searchParams: Promise<{ w?: string | string[] }>;
}) {
  const window = parseWindow((await searchParams).w);
  const digest = await getDigest(window);
  const routineCount = digest.routine.reduce((n, i) => n + 1 + i.related_count, 0);
  const empty = digest.filings === 0;

  return (
    <>
      <header className="mb-3 flex items-baseline justify-between">
        <h1 className="text-base font-semibold text-n-50">Digest</h1>
        <span className="num text-xs text-n-500">
          {digest.filings} filings · {digest.cards.length} material
        </span>
      </header>
      <WindowPicker
        options={Object.keys(WINDOWS) as WindowKey[]}
        active={window}
        basePath="/"
        anchor={digest.anchor}
      />

      {empty ? (
        <p className="mt-6 text-n-400">
          Nothing filed in the last {window}.
          {WIDER[window] && (
            <> Try <a href={`/?w=${WIDER[window]}`} className="text-accent">{WIDER[window]}</a>.</>
          )}
        </p>
      ) : (
        <>
          <section className="mt-2">{digest.cards.map((item) => <FilingCard key={item.id} item={item} />)}</section>
          {digest.rows.length > 0 && (
            <ul className="mt-1">{digest.rows.map((item) => <FilingRow key={item.id} item={item} />)}</ul>
          )}
          <RoutineLine items={digest.routine} count={routineCount} />
        </>
      )}
    </>
  );
}

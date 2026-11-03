"use client";
// Runs live research for one uncached ticker and shows each stage as the server reports it.
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { LiveEvent, Stage } from "@/lib/live/pipeline";

const STAGES: { key: Stage; label: string }[] = [
  { key: "finding", label: "Finding documents" },
  { key: "extracting", label: "Extracting" },
  { key: "analysing", label: "Analysing" },
];

type StageState = { status: "waiting" | "active" | "done"; detail?: string };

export default function ResearchProgress({ symbol, runsLeft }: { symbol: string; runsLeft: number }) {
  const router = useRouter();
  const started = useRef(false);
  const [stages, setStages] = useState<Record<Stage, StageState>>({
    finding: { status: "waiting" }, extracting: { status: "waiting" }, analysing: { status: "waiting" },
  });
  const [error, setError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    // Development mode mounts effects twice; one run per ticker is a quota decision.
    if (started.current) return;
    started.current = true;

    const clock = setInterval(() => setSeconds((s) => s + 1), 1000);
    const apply = (event: LiveEvent) => {
      if (event.type === "stage") {
        setStages((s) => ({ ...s, [event.stage]: { status: event.status, detail: event.detail } }));
      } else if (event.type === "error") {
        setError(event.message);
      } else {
        setFinished(true);
        router.refresh();
      }
    };

    run(symbol, apply)
      .catch(() => setError("Lost the connection to the server before the run finished. Reload to see if it completed."))
      .finally(() => clearInterval(clock));
    return () => clearInterval(clock);
  }, [symbol, router]);

  return (
    <div className="mt-4 border-y border-n-800 py-3">
      <div className="flex items-baseline justify-between">
        <p className="text-n-50">{symbol} · live research</p>
        <span className="num text-xs text-n-500">{seconds}s</span>
      </div>
      <ol className="mt-3 space-y-2">
        {STAGES.map(({ key, label }, i) => {
          const s = stages[key];
          return (
            <li key={key} className="grid grid-cols-[20px_1fr_auto] items-baseline gap-y-0.5">
              <span className={`num text-xs ${s.status === "active" ? "text-accent" : "text-n-500"}`}>{i + 1}</span>
              <span className={s.status === "waiting" ? "text-n-500" : "text-n-50"}>{label}</span>
              <span className="text-xs text-n-500">
                {s.status === "done" ? "done" : s.status === "active" ? (error ? "stopped" : "running…") : ""}
              </span>
              {s.detail && <span className="num col-start-2 col-span-2 text-xs text-n-400">{s.detail}</span>}
            </li>
          );
        })}
      </ol>
      {error ? (
        <p className="mt-3 border-l border-accent/60 pl-3 text-sm text-n-200">{error}</p>
      ) : finished ? (
        <p className="mt-3 text-xs text-n-400">Saved. Loading the analysis…</p>
      ) : (
        <p className="mt-3 text-xs text-n-500">
          Reads screener.in, the latest concall transcript and two cheap model calls. Usually 20-40s.
          Uses one of today&apos;s live runs ({runsLeft} left).
        </p>
      )}
    </div>
  );
}

async function run(symbol: string, apply: (e: LiveEvent) => void): Promise<void> {
  const resp = await fetch("/api/research", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol }),
  });
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let terminal = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line) as LiveEvent;
      terminal ||= event.type !== "stage";
      apply(event);
    }
  }
  // A stream that ends without done or error was cut off, typically by the platform time limit.
  if (!terminal) throw new Error("stream ended early");
}

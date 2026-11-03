// One live research run, start to finish, reporting each stage as it begins and ends.
import "server-only";
import { guidance, snapshot } from "./analyse";
import { LiveError } from "./errors";
import { transcriptText } from "./pdf";
import { fetchScreener } from "./screener";
import { knownSymbol, LIVE_RUNS_PER_DAY, liveRunsToday, saveLiveRun } from "./store";

export type Stage = "finding" | "extracting" | "analysing";

export type LiveEvent =
  | { type: "stage"; stage: Stage; status: "active" | "done"; detail?: string }
  | { type: "done"; symbol: string; cached: boolean }
  | { type: "error"; code: string; message: string };

export async function runLive(symbol: string, signal: AbortSignal, emit: (e: LiveEvent) => void): Promise<void> {
  const known = await knownSymbol(symbol);
  if (known.analysed) {
    emit({ type: "done", symbol, cached: true });
    return;
  }
  // A batch ingested company is never rebuilt live: its analysis belongs to the strong tier.
  if (known.ingested) {
    throw new LiveError("pending",
      `${symbol} is covered by the batch ingest and its analysis has not been built yet. Check back after the next run.`);
  }
  if ((await liveRunsToday()) >= LIVE_RUNS_PER_DAY) {
    throw new LiveError("cap",
      `Today's ${LIVE_RUNS_PER_DAY} live research runs are used. Cached companies still load instantly; new tickers open again after midnight IST.`);
  }

  emit({ type: "stage", stage: "finding", status: "active" });
  const page = await fetchScreener(symbol, signal);
  const transcript = page.transcript!;
  emit({ type: "stage", stage: "finding", status: "done",
    detail: `${page.name} · ${transcript.period} transcript · ${page.quarters.length} quarters` });

  emit({ type: "stage", stage: "extracting", status: "active" });
  const { text, pages } = await transcriptText(transcript.url, signal);
  emit({ type: "stage", stage: "extracting", status: "done",
    detail: `${pages} pages · ${text.length.toLocaleString("en-IN")} characters` });

  emit({ type: "stage", stage: "analysing", status: "active" });
  const company = { symbol, name: page.name, sector: page.sector };
  const [brief, quotes] = await Promise.all([
    snapshot(company, page.quarters, { text, period: transcript.period }, signal),
    guidance(text, signal),
  ]);
  emit({ type: "stage", stage: "analysing", status: "done",
    detail: `${quotes.data.length} verbatim guidance quotes kept, ${quotes.rejected} dropped` });

  await saveLiveRun({
    symbol,
    company,
    quarters: page.quarters,
    payload: {
      ...brief.data,
      source: "live",
      tier: "cheap",
      guidance: quotes.data,
      guidance_rejected: quotes.rejected,
      transcript_url: transcript.url,
      transcript_period: transcript.period,
      statement_basis: page.basis,
    },
    model: brief.model,
    promptTokens: brief.promptTokens + quotes.promptTokens,
    outputTokens: brief.outputTokens + quotes.outputTokens,
  });
  emit({ type: "done", symbol, cached: false });
}

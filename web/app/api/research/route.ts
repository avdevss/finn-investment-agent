// Live research endpoint: runs the pipeline for one symbol and streams NDJSON stage events.
import { parseSymbol } from "@/lib/research";
import { DEADLINE_MS, toLiveError } from "@/lib/live/errors";
import { runLive, type LiveEvent } from "@/lib/live/pipeline";

export const runtime = "nodejs";
export const maxDuration = 60;
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const body = await request.json().catch(() => ({}));
  const symbol = parseSymbol((body as { symbol?: unknown }).symbol as string | undefined);

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const emit = (event: LiveEvent) => controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
      if (!symbol) {
        emit({ type: "error", code: "invalid", message: "Enter an NSE symbol: letters, digits, & or -." });
        controller.close();
        return;
      }
      const deadline = AbortSignal.timeout(DEADLINE_MS);
      try {
        await runLive(symbol, deadline, emit);
      } catch (e) {
        const err = toLiveError(e, deadline);
        if (err.code === "failed") console.error(`live research ${symbol}:`, e);
        emit({ type: "error", code: err.code, message: err.message });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Cache-Control": "no-store",
      // Keeps proxies from buffering the stage events until the run ends.
      "X-Accel-Buffering": "no",
    },
  });
}

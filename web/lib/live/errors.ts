// Every way a live research run can stop, each with the message the investor sees.

export type LiveErrorCode =
  | "invalid" | "cap" | "blocked" | "not_found" | "no_transcript"
  | "scanned" | "timeout" | "quota" | "pending" | "failed";

export class LiveError extends Error {
  constructor(public code: LiveErrorCode, message: string) {
    super(message);
  }
}

export const DEADLINE_MS = 55_000;

// Turns anything thrown during a run into the one message shown on screen.
export function toLiveError(e: unknown, signal: AbortSignal): LiveError {
  if (e instanceof LiveError) return e;
  if (signal.aborted) {
    return new LiveError("timeout",
      `Stopped at ${DEADLINE_MS / 1000}s before finishing. Nothing was saved; try again in a minute.`);
  }
  const detail = e instanceof Error ? e.message : String(e);
  return new LiveError("failed", `The run failed unexpectedly: ${detail.slice(0, 200)}`);
}

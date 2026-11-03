// Outbound fetch for live research: the ingest header sets, a per-host throttle and
// growing retries on 429/5xx, all bounded by the run's deadline. Mirrors ingest/http.py.
import "server-only";

export const CHROME_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36";

export const SCREENER_HEADERS: Record<string, string> = {
  "User-Agent": CHROME_UA,
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "en-US,en;q=0.9",
};

export const BSE_PDF_HEADERS: Record<string, string> = {
  "User-Agent": CHROME_UA,
  Accept: "application/pdf,*/*",
  Referer: "https://www.bseindia.com/",
};

const RETRY_STATUS = new Set([408, 425, 429, 500, 502, 503, 504, 509]);
const MIN_INTERVAL_MS: Record<string, number> = {
  "www.screener.in": 1500,
  "www.bseindia.com": 400,
};
const DEFAULT_INTERVAL_MS = 500;
const lastHit = new Map<string, number>();

export function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function throttle(host: string, signal: AbortSignal): Promise<void> {
  const interval = MIN_INTERVAL_MS[host] ?? DEFAULT_INTERVAL_MS;
  const wait = interval - (Date.now() - (lastHit.get(host) ?? 0));
  if (wait > 0) await sleep(wait, signal);
  lastHit.set(host, Date.now());
}

export class HttpStatusError extends Error {
  constructor(public status: number, url: string) {
    super(`HTTP ${status} from ${new URL(url).host}`);
  }
}

// GET with throttle and retries. Throws HttpStatusError on a final non-2xx.
export async function get(url: string, headers: Record<string, string>, signal: AbortSignal,
  attempts = 3, backoffMs = 1500): Promise<Response> {
  const host = new URL(url).host;
  let last: unknown;
  for (let attempt = 0; attempt < attempts; attempt++) {
    await throttle(host, signal);
    try {
      const resp = await fetch(url, { headers, signal, redirect: "follow", cache: "no-store" });
      if (RETRY_STATUS.has(resp.status) && attempt < attempts - 1) {
        await resp.body?.cancel();
        await sleep(backoffMs * 2 ** attempt + Math.random() * 500, signal);
        continue;
      }
      if (!resp.ok) throw new HttpStatusError(resp.status, url);
      return resp;
    } catch (e) {
      if (signal.aborted || e instanceof HttpStatusError) throw e;
      last = e;
      await sleep(backoffMs * 2 ** attempt + Math.random() * 500, signal);
    }
  }
  throw last instanceof Error ? last : new Error(`${url} failed after ${attempts} attempts`);
}

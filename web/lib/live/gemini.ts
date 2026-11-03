// Gemini client for live research: cheap tier only, retries inside the run's deadline, and a
// daily quota wall reported as such. Mirrors the request shape of ingest/llm.py.
import "server-only";
import { LiveError } from "./errors";
import { sleep } from "./http";

const API_BASE = "https://generativelanguage.googleapis.com/v1beta/models";
const RETRY_STATUS = new Set([429, 500, 502, 503, 504]);
const ATTEMPTS = 4;
const BASE_WAIT_MS = 3000;
// An advised wait this long is a daily quota, not congestion; no backoff outlasts it.
const QUOTA_WALL_S = 300;

export type JsonResult<T> = { data: T; model: string; promptTokens: number; outputTokens: number };

export function cheapModel(): string {
  const model = process.env.CHEAP_MODEL;
  if (!model) throw new LiveError("failed", "CHEAP_MODEL is not configured on the server.");
  if (model.includes("preview")) throw new LiveError("failed", `CHEAP_MODEL=${model} is a preview model.`);
  return model;
}

export async function generateJson<T>(opts: {
  prompt: string; system: string; schema: object; maxTokens: number; signal: AbortSignal;
}): Promise<JsonResult<T>> {
  const key = process.env.GEMINI_API_KEY;
  if (!key) throw new LiveError("failed", "GEMINI_API_KEY is not configured on the server.");
  const model = cheapModel();
  const body = JSON.stringify({
    contents: [{ role: "user", parts: [{ text: opts.prompt }] }],
    systemInstruction: { parts: [{ text: opts.system }] },
    generationConfig: {
      temperature: 0,
      maxOutputTokens: opts.maxTokens,
      responseMimeType: "application/json",
      responseSchema: opts.schema,
    },
  });

  for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
    const resp = await fetch(`${API_BASE}/${model}:generateContent`, {
      method: "POST",
      headers: { "x-goog-api-key": key, "Content-Type": "application/json" },
      body,
      signal: opts.signal,
      cache: "no-store",
    });
    const payload = await resp.json().catch(() => ({}));

    if (resp.ok) return parse<T>(payload, model);

    const advised = advisedDelaySeconds(resp, payload);
    if (resp.status === 429 && advised !== null && advised > QUOTA_WALL_S) {
      throw new LiveError("quota",
        `Today's ${model} quota is used up (resets in about ${Math.ceil(advised / 3600)}h). Cached companies still load.`);
    }
    if (!RETRY_STATUS.has(resp.status)) {
      throw new LiveError("failed", `The model rejected the request: HTTP ${resp.status} ${errorMessage(payload)}`);
    }
    if (attempt < ATTEMPTS - 1) {
      const wait = advised !== null ? advised * 1000 : BASE_WAIT_MS * 2 ** attempt + Math.random() * 1000;
      await sleep(wait, opts.signal);
    }
  }
  throw new LiveError("quota", `${model} is overloaded or rate limited right now. Try again in a few minutes.`);
}

type Payload = {
  candidates?: { content?: { parts?: { text?: string }[] }; finishReason?: string }[];
  usageMetadata?: { promptTokenCount?: number; candidatesTokenCount?: number };
  error?: { message?: string; details?: { "@type"?: string; retryDelay?: string }[] };
};

function parse<T>(payload: Payload, model: string): JsonResult<T> {
  const candidate = payload.candidates?.[0];
  const text = (candidate?.content?.parts ?? []).map((p) => p.text ?? "").join("");
  if (!text.trim()) {
    throw new LiveError("failed", `The model returned nothing (finishReason ${candidate?.finishReason ?? "none"}).`);
  }
  let data: T;
  try {
    data = JSON.parse(text) as T;
  } catch {
    throw new LiveError("failed", `The model returned malformed JSON (finishReason ${candidate?.finishReason ?? "none"}).`);
  }
  return {
    data,
    model,
    promptTokens: payload.usageMetadata?.promptTokenCount ?? 0,
    outputTokens: payload.usageMetadata?.candidatesTokenCount ?? 0,
  };
}

function advisedDelaySeconds(resp: Response, payload: Payload): number | null {
  const header = Number(resp.headers.get("retry-after"));
  if (Number.isFinite(header) && header > 0) return header;
  const info = payload.error?.details?.find((d) => d["@type"]?.endsWith("RetryInfo"));
  const m = /^(\d+(?:\.\d+)?)s$/.exec(info?.retryDelay ?? "");
  return m ? Number(m[1]) : null;
}

function errorMessage(payload: Payload): string {
  return (payload.error?.message ?? "").slice(0, 200);
}

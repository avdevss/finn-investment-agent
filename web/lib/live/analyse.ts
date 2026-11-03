// The two cheap model calls of a live run: snapshot with bull and bear, and management
// guidance quotes, each kept only if it is verbatim in the transcript.
import "server-only";
import type { AnalysisPayload, GuidanceQuote } from "@/lib/types";
import { generateJson, type JsonResult } from "./gemini";
import type { QuarterRow } from "./screener";

const SNAPSHOT_CHARS = 60_000;
const GUIDANCE_CHARS = 150_000;

// Same brief and schema as ingest/analysis.py, so a live row reads like a batch row.
const SNAPSHOT_SYSTEM =
  "You brief a professional investor on an Indian listed company, using only the " +
  "two sources given to you: an extract of the company's own earnings call " +
  "transcript, and its own reported quarterly figures.\n" +
  "Hard rules:\n" +
  "1. Use only these sources. Do not use anything you know about the company from " +
  "elsewhere. Do not add a fact, a number, a competitor or a market size that is " +
  "not in the text.\n" +
  "2. Every number you state must appear in the sources.\n" +
  "3. If the sources do not support a point, leave it out. A short brief is better " +
  "than a padded one.\n" +
  "4. No hedging filler and no investment advice. Write plainly.\n" +
  "Return: snapshot (2 sentences on what the business is), how_it_makes_money " +
  "(1-2 sentences), segments (the operating segments the call discusses, with one " +
  "line each), financial_read (3-4 sentences on what the reported numbers show), " +
  "bull (3-4 specific points the sources support), bear (3-4 specific risks or " +
  "weaknesses the sources support, including anything management was pressed on).";

const SNAPSHOT_SCHEMA = {
  type: "object",
  properties: {
    snapshot: { type: "string" },
    how_it_makes_money: { type: "string" },
    segments: {
      type: "array",
      items: {
        type: "object",
        properties: { name: { type: "string" }, note: { type: "string" } },
        required: ["name", "note"],
      },
    },
    financial_read: { type: "string" },
    bull: { type: "array", items: { type: "string" } },
    bear: { type: "array", items: { type: "string" } },
  },
  required: ["snapshot", "how_it_makes_money", "financial_read", "bull", "bear"],
};

const GUIDANCE_SYSTEM =
  "You read an earnings call transcript and copy out management's forward looking " +
  "statements: targets, guidance, outlook, planned capex, expected margins or growth, timelines.\n" +
  "Hard rules:\n" +
  "1. Copy each sentence exactly as it appears in the text, character for character. " +
  "Do not paraphrase, shorten, merge or fix anything.\n" +
  "2. Only sentences spoken by company management. Never anything said by an analyst, " +
  "a questioner or the moderator.\n" +
  "3. Only statements about the future. No past results.\n" +
  "4. Use only the text given. No outside knowledge.\n" +
  "Return at most 12, the most specific and quantified first. topic is 2-4 words.";

const GUIDANCE_SCHEMA = {
  type: "object",
  properties: {
    guidance: {
      type: "array",
      items: {
        type: "object",
        properties: { quote: { type: "string" }, topic: { type: "string" } },
        required: ["quote", "topic"],
      },
    },
  },
  required: ["guidance"],
};

export const normalise = (text: string) => text.replace(/\s+/g, " ").trim();

function quarterTable(rows: QuarterRow[]): string {
  const cell = (v: number | null, w = 12) => (v === null ? "-" : Math.round(v).toLocaleString("en-IN")).padStart(w);
  const lines = [`${"period".padEnd(9)}${"sales".padStart(12)}${"op profit".padStart(12)}${"opm %".padStart(8)}${"pbt".padStart(12)}${"net".padStart(12)}`];
  for (const r of rows.slice(-8)) {
    lines.push(`${r.period.padEnd(9)}${cell(r.sales)}${cell(r.operating_profit)}${cell(r.opm, 8)}${cell(r.pbt)}${cell(r.net_profit)}`);
  }
  return lines.join("\n");
}

export async function snapshot(company: { symbol: string; name: string; sector: string | null },
  quarters: QuarterRow[], transcript: { text: string; period: string }, signal: AbortSignal)
  : Promise<JsonResult<AnalysisPayload>> {
  const prompt =
    `Company: ${company.name} (${company.symbol}), sector ${company.sector ?? "unknown"}.\n\n` +
    `--- source 1: reported quarterly figures (the company's own) ---\n${quarterTable(quarters)}\n\n` +
    `--- source 2: the company's own earnings call ---\nEarnings call, ${transcript.period}:\n` +
    transcript.text.slice(0, SNAPSHOT_CHARS);
  return generateJson<AnalysisPayload>({
    prompt, system: SNAPSHOT_SYSTEM, schema: SNAPSHOT_SCHEMA, maxTokens: 4096, signal,
  });
}

export async function guidance(text: string, signal: AbortSignal)
  : Promise<JsonResult<GuidanceQuote[]> & { rejected: number }> {
  const source = text.slice(0, GUIDANCE_CHARS);
  const result = await generateJson<{ guidance: GuidanceQuote[] }>({
    prompt: `--- earnings call transcript ---\n${source}`,
    system: GUIDANCE_SYSTEM, schema: GUIDANCE_SCHEMA, maxTokens: 4096, signal,
  });
  // The evidence must be the document's own words: anything not found verbatim is dropped.
  const haystack = normalise(source);
  const seen = new Set<string>();
  const kept: GuidanceQuote[] = [];
  let rejected = 0;
  for (const item of result.data.guidance ?? []) {
    const quote = normalise(item.quote ?? "");
    if (!haystack.includes(quote)) {
      rejected++;
      continue;
    }
    // Fragments too short to stand as evidence, and repeats, are skipped without counting.
    if (quote.length < 20 || seen.has(quote)) continue;
    seen.add(quote);
    kept.push({ quote, topic: item.topic });
  }
  return { ...result, data: kept, rejected };
}

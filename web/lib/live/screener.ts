// Screener company page for live research: name, sector, newest concall transcript and the
// quarterly table. Mirrors ingest/documents.py::concalls and ingest/quarters.py::parse.
import "server-only";
import { parse, type HTMLElement } from "node-html-parser";
import { LiveError } from "./errors";
import { get, HttpStatusError, SCREENER_HEADERS } from "./http";
import { parseMonthYear, quarterLabel, reportedQuarterEnd } from "./periods";

export type QuarterRow = {
  period_end: string;
  period: string;
  sales: number | null;
  operating_profit: number | null;
  opm: number | null;
  other_income: number | null;
  interest: number | null;
  depreciation: number | null;
  pbt: number | null;
  net_profit: number | null;
};

export type ScreenerPage = {
  name: string;
  sector: string | null;
  basis: "consolidated" | "standalone";
  transcript: { url: string; period: string } | null;
  quarters: QuarterRow[];
};

type Metric = Exclude<keyof QuarterRow, "period_end" | "period">;

// Screener's own row labels; lenders use the financing variants.
const ROW_LABELS: Record<Metric, string[]> = {
  sales: ["sales", "revenue"],
  operating_profit: ["operating profit", "financing profit"],
  opm: ["opm %", "financing margin %"],
  other_income: ["other income"],
  interest: ["interest"],
  depreciation: ["depreciation"],
  pbt: ["profit before tax"],
  net_profit: ["net profit"],
};

const clean = (text: string) => text.replace(/ /g, " ").replace(/\+/g, "").replace(/\s+/g, " ").trim();

function toNumber(cell: string): number | null {
  const n = Number(cell.replace(/[,%\s]/g, ""));
  return cell.trim() === "" || !Number.isFinite(n) ? null : n;
}

export async function fetchScreener(symbol: string, signal: AbortSignal): Promise<ScreenerPage> {
  const consolidated = await fetchPage(symbol, "consolidated", signal);
  if (consolidated && consolidated.transcript && consolidated.quarters.length > 0) return consolidated;

  const standalone = await fetchPage(symbol, "standalone", signal);
  const page = [standalone, consolidated].find((p) => p?.transcript) ?? standalone ?? consolidated;
  if (!page) {
    throw new LiveError("not_found", `${symbol} is not on screener.in. Check the NSE symbol.`);
  }
  if (!page.transcript) {
    throw new LiveError("no_transcript",
      `${page.name} has no concall transcript on screener.in, so there is nothing primary to analyse.`);
  }
  return page;
}

async function fetchPage(symbol: string, basis: ScreenerPage["basis"], signal: AbortSignal)
  : Promise<ScreenerPage | null> {
  const url = `https://www.screener.in/company/${encodeURIComponent(symbol)}/${basis === "consolidated" ? "consolidated/" : ""}`;
  let html: string;
  try {
    html = await (await get(url, SCREENER_HEADERS, signal)).text();
  } catch (e) {
    if (e instanceof HttpStatusError && e.status === 404) return null;
    if (signal.aborted) throw e;
    throw new LiveError("blocked",
      "screener.in refused the request from this server. Cached companies still work; try again later.");
  }
  const root = parse(html);
  const name = clean(root.querySelector("h1")?.text ?? "");
  if (!name) return null;
  return {
    name,
    sector: clean(root.querySelector('a[title="Sector"]')?.text ?? "") || null,
    basis,
    transcript: latestTranscript(root),
    quarters: quarters(root),
  };
}

// Each concall entry reads "Oct 2026 | Transcript | AI Summary | PPT"; the newest comes first.
function latestTranscript(root: HTMLElement): ScreenerPage["transcript"] {
  // The "Concalls" h3 sits in a header row; its list is a sibling one level up.
  const heading = root.querySelectorAll("#documents h3").find((h) => /concall/i.test(h.text));
  const list = root.querySelector("#documents .concalls ul.list-links")
    ?? heading?.parentNode?.parentNode?.querySelector("ul.list-links");
  for (const item of list?.querySelectorAll("li") ?? []) {
    const link = item.querySelectorAll("a").find((a) => clean(a.text).toLowerCase() === "transcript");
    const when = parseMonthYear(clean(item.text).slice(0, 40));
    const href = link?.getAttribute("href");
    if (!href || !when) continue;
    const end = reportedQuarterEnd(when.year, when.month);
    return { url: new URL(href, "https://www.screener.in").toString(), period: quarterLabel(end.year, end.month) };
  }
  return null;
}

function quarters(root: HTMLElement): QuarterRow[] {
  const table = root.querySelector("#quarters table");
  if (!table) return [];
  // Column dates come from data-date-key, so no header text needs parsing.
  const columns = table.querySelectorAll("thead th")
    .map((th, index) => ({ index, key: th.getAttribute("data-date-key") ?? "" }))
    .filter((c) => /^\d{4}-(03|06|09|12)-\d{2}$/.test(c.key));

  const values: Partial<Record<Metric, (number | null)[]>> = {};
  for (const tr of table.querySelectorAll("tbody tr")) {
    const cells = tr.querySelectorAll("td").map((td) => clean(td.text));
    const label = (cells[0] ?? "").toLowerCase();
    const metric = (Object.keys(ROW_LABELS) as Metric[]).find((m) => ROW_LABELS[m].includes(label));
    if (metric && !values[metric]) values[metric] = cells.map(toNumber);
  }

  return columns.map(({ index, key }) => {
    const [year, month] = key.split("-").map(Number);
    const row = { period_end: key, period: quarterLabel(year, month) } as QuarterRow;
    for (const metric of Object.keys(ROW_LABELS) as Metric[]) row[metric] = values[metric]?.[index] ?? null;
    return row;
  }).filter((row) => row.sales !== null || row.net_profit !== null);
}

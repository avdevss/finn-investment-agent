// Research lookups: which symbols already carry a cached analysis.
import "server-only";
import { sql } from "./db";

const SYMBOL = /^[A-Z0-9&-]{1,20}$/;

export function parseSymbol(raw: string | string[] | undefined): string | null {
  if (typeof raw !== "string") return null;
  const symbol = raw.trim().toUpperCase();
  return SYMBOL.test(symbol) ? symbol : null;
}

export async function getCachedSymbols(): Promise<{ symbol: string; in_portfolio: boolean }[]> {
  return sql<{ symbol: string; in_portfolio: boolean }[]>`
    select a.symbol, c.in_portfolio from analysis a join companies c on c.symbol = a.symbol
    order by c.in_portfolio, a.symbol`;
}

// The fiscal labels live research needs: the subset of ingest/periods.py for one transcript.

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const QUARTER_END_MONTHS = [3, 6, 9, 12];

// "Oct 2026" -> { year: 2026, month: 10 }
export function parseMonthYear(text: string): { year: number; month: number } | null {
  const m = /\b([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\b/.exec(text);
  if (!m) return null;
  const month = MONTHS.indexOf(m[1].toLowerCase()) + 1;
  return month ? { year: Number(m[2]), month } : null;
}

// A call held in Oct 2026 reports on the quarter that ended Sep 2026.
export function reportedQuarterEnd(year: number, month: number): { year: number; month: number } {
  const earlier = QUARTER_END_MONTHS.filter((q) => q < month);
  return earlier.length ? { year, month: Math.max(...earlier) } : { year: year - 1, month: 12 };
}

// Q2FY27 for the quarter ending Sep 2026. Fiscal years run April to March.
export function quarterLabel(year: number, month: number): string {
  const fiscal = month <= 3 ? year : year + 1;
  const quarter = { 6: 1, 9: 2, 12: 3, 3: 4 }[month];
  if (!quarter) throw new Error(`${year}-${month} is not a quarter end`);
  return `Q${quarter}FY${String(fiscal % 100).padStart(2, "0")}`;
}

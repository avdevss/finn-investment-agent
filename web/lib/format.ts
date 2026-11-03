// Number, percentage and time formatting. Everything shown on screen goes through here.

const IST = "Asia/Kolkata";

const grouped = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const groupedFine = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

export function num(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return Math.abs(n) >= 100 ? grouped.format(n) : groupedFine.format(n);
}

export function signedPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(digits)}%`;
}

export function pctChange(current: number | null, base: number | null): number | null {
  if (current === null || base === null || base === 0) return null;
  return ((current - base) / Math.abs(base)) * 100;
}

export function istDateTime(value: Date | string): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: IST, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function istDate(value: Date | string): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: IST, day: "numeric", month: "short", year: "numeric",
  }).format(new Date(value));
}


// Transcript dates are month ends derived from screener's month labels, so only the month is real.
export function callMonth(isoDate: string): string {
  const [year, month] = isoDate.split("-").map(Number);
  return new Intl.DateTimeFormat("en-IN", { month: "short", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, 1)));
}

export function callMonthsIn(text: string): string {
  return text.replace(/call dated (\d{4}-\d{2}-\d{2})/g, (_, d: string) => `call in ${callMonth(d)}`);
}

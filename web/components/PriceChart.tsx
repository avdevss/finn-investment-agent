"use client";
// One year of daily closes over volume: two charts on a shared date axis, never two scales on one.
import { Bar, BarChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PricePoint } from "@/lib/company";

const INK = "#d5d8dd";
const MUTED = "#5d626b";
const GRID = "#2a2d33";
const SYNC = "price-volume";

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });
const month = (d: string) =>
  new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { month: "short", year: "2-digit" });

const tooltipStyle = {
  contentStyle: { background: "#131417", border: `1px solid ${GRID}`, borderRadius: 0, fontSize: 11 },
  labelStyle: { color: "#8a9099" },
  itemStyle: { color: INK, fontVariantNumeric: "tabular-nums" },
  cursor: { stroke: MUTED, strokeWidth: 1 },
};

export default function PriceChart({ data }: { data: PricePoint[] }) {
  if (data.length === 0) return <p className="py-6 text-n-500">No price history stored.</p>;
  return (
    <div className="num text-xs">
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} syncId={SYNC} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
          <XAxis dataKey="date" hide />
          <YAxis
            domain={["auto", "auto"]} orientation="right" width={52} tickLine={false} axisLine={false}
            tick={{ fill: MUTED, fontSize: 11 }} tickFormatter={(v: number) => inr.format(v)}
          />
          <Tooltip {...tooltipStyle} formatter={(v: number) => [`₹${inr.format(v)}`, "Close"]} />
          <Line type="linear" dataKey="close" stroke={INK} strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
      <ResponsiveContainer width="100%" height={56}>
        <BarChart data={data} syncId={SYNC} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <XAxis
            dataKey="date" tickLine={false} axisLine={{ stroke: GRID }} minTickGap={40}
            tick={{ fill: MUTED, fontSize: 11 }} tickFormatter={month}
          />
          <YAxis orientation="right" width={52} tickLine={false} axisLine={false}
            tick={{ fill: MUTED, fontSize: 11 }} tickCount={2} tickFormatter={(v: number) => compact.format(v)} />
          <Tooltip {...tooltipStyle} cursor={{ fill: GRID }}
            formatter={(v: number) => [compact.format(v), "Volume"]} />
          <Bar dataKey="volume" fill={MUTED} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

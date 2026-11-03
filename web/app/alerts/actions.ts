"use server";
// Persists one alert rule. Bounds are checked here, not trusted from the form.
import { revalidatePath } from "next/cache";
import { sql } from "@/lib/db";
import { CATEGORIES } from "@/lib/categories";

const BOUNDS = { price_move: [1, 20], volume_spike: [1, 10] } as const;

export type SaveResult = { ok: true } | { ok: false; error: string };

export async function saveRule(form: FormData): Promise<SaveResult> {
  const kind = String(form.get("kind"));
  const enabled = form.get("enabled") === "on";

  if (kind === "price_move" || kind === "volume_spike") {
    const threshold = Number(form.get("threshold"));
    const [lo, hi] = BOUNDS[kind];
    if (!Number.isFinite(threshold) || threshold < lo || threshold > hi) {
      return { ok: false, error: `threshold must be between ${lo} and ${hi}` };
    }
    await sql`update alert_rules set threshold = ${threshold}, enabled = ${enabled},
              updated_at = now() where kind = ${kind}`;
  } else if (kind === "category") {
    const categories = form.getAll("categories").map(String);
    if (categories.some((c) => !CATEGORIES.includes(c))) {
      return { ok: false, error: "unknown category" };
    }
    await sql`update alert_rules set categories = ${categories}, enabled = ${enabled},
              updated_at = now() where kind = 'category'`;
  } else {
    return { ok: false, error: "unknown rule" };
  }

  revalidatePath("/alerts");
  return { ok: true };
}

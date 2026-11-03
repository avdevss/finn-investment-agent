"use client";
// Alert rule controls. Each rule saves on its own through the server action.
import { useState, useTransition } from "react";
import { saveRule, type SaveResult } from "@/app/alerts/actions";
import { CATEGORIES, categoryLabel } from "@/lib/categories";
import type { Rule } from "@/lib/alerts";

export default function AlertRulesForm({ price, volume, category }: {
  price: Rule; volume: Rule; category: Rule;
}) {
  return (
    <div className="border-y border-n-800">
      <ThresholdRule rule={price} label="Price move" unit="%" prefix="±" min={1} max={20} step={0.5} />
      <ThresholdRule rule={volume} label="Volume spike" unit="× 20d avg" prefix="" min={1} max={10} step={0.1} />
      <CategoryRule rule={category} />
    </div>
  );
}

function useSave() {
  const [pending, start] = useTransition();
  const [result, setResult] = useState<SaveResult | null>(null);
  const submit = (form: FormData) => start(async () => setResult(await saveRule(form)));
  const status = pending ? "Saving…" : result && !result.ok ? result.error : result ? "Saved" : "";
  return { pending, submit, status, dirty: () => setResult(null) };
}

function RuleHeader({ label, enabled, onToggle }: {
  label: string; enabled: boolean; onToggle: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between">
      <span className="text-sm font-medium text-n-50">{label}</span>
      <span className="flex items-center gap-2 text-xs text-n-400">
        {enabled ? "On" : "Off"}
        <input type="checkbox" name="enabled" checked={enabled} onChange={(e) => onToggle(e.target.checked)} />
      </span>
    </label>
  );
}

function SaveBar({ pending, status }: { pending: boolean; status: string }) {
  return (
    <div className="mt-2 flex items-center justify-end gap-3">
      <span className="text-xs text-n-500">{status}</span>
      <button
        type="submit"
        disabled={pending}
        className="border border-n-700 px-3 py-1 text-xs font-medium text-n-50 disabled:text-n-500"
      >
        Save
      </button>
    </div>
  );
}

function ThresholdRule({ rule, label, unit, prefix, min, max, step }: {
  rule: Rule; label: string; unit: string; prefix: string; min: number; max: number; step: number;
}) {
  const [threshold, setThreshold] = useState(rule.threshold ?? min);
  const [enabled, setEnabled] = useState(rule.enabled);
  const { pending, submit, status, dirty } = useSave();
  return (
    <form action={submit} onChange={dirty} className="border-b border-n-800 py-3">
      <input type="hidden" name="kind" value={rule.kind} />
      <RuleHeader label={label} enabled={enabled} onToggle={setEnabled} />
      <div className="mt-2 flex items-center gap-3">
        <input
          type="range" name="threshold" min={min} max={max} step={step} value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          disabled={!enabled}
          className="flex-1"
        />
        <span className="num w-24 text-right text-sm text-n-50">
          {prefix}{threshold.toFixed(1)} {unit}
        </span>
      </div>
      {/* a disabled range is left out of FormData, so the value is carried here too */}
      {!enabled && <input type="hidden" name="threshold" value={threshold} />}
      <SaveBar pending={pending} status={status} />
    </form>
  );
}

function CategoryRule({ rule }: { rule: Rule }) {
  const [selected, setSelected] = useState<string[]>(rule.categories);
  const [enabled, setEnabled] = useState(rule.enabled);
  const { pending, submit, status, dirty } = useSave();
  const toggle = (c: string) =>
    setSelected((s) => (s.includes(c) ? s.filter((x) => x !== c) : [...s, c]));
  return (
    <form action={submit} onChange={dirty} className="py-3">
      <input type="hidden" name="kind" value="category" />
      <RuleHeader label="Filing category" enabled={enabled} onToggle={setEnabled} />
      <div className="mt-2 flex flex-wrap gap-1.5">
        {CATEGORIES.map((c) => {
          const on = selected.includes(c);
          return (
            <label
              key={c}
              className={`cursor-pointer border px-2 py-1 text-xs ${
                on ? "border-accent/70 text-n-50" : "border-n-700 text-n-500"
              }`}
            >
              <input type="checkbox" name="categories" value={c} checked={on}
                onChange={() => toggle(c)} className="sr-only" />
              {categoryLabel(c)}
            </label>
          );
        })}
      </div>
      <SaveBar pending={pending} status={status} />
    </form>
  );
}

// Row shapes and the JSON payloads the ingest pipeline writes into jsonb columns.

export type ScoreParts = {
  category: string;
  category_weight: number;
  price_component: number;
  volume_component: number;
  pct_change: number | null;
  vol_ratio: number | null;
  price_date: string | null;
  price_basis: string;
  total: number;
  notice_cap?: { uncapped: number; reason: string };
};

export type Figure = {
  current: number | null;
  prior_quarter: number | null;
  year_ago: number | null;
  label: string;
  qoq_pct: number | null;
  yoy_pct: number | null;
};

export type Extracted = {
  status?: string;
  reason?: string;
  period?: string;
  period_end?: string;
  basis?: string;
  units?: string | null;
  page?: number;
  extracted_by?: string | null;
  ebitda_basis?: string;
  columns?: { current: string; prior_quarter: string; year_ago: string };
  metrics?: {
    revenue?: Figure | null;
    ebitda?: Figure | null;
    pbt?: Figure | null;
    pat?: Figure | null;
  } | null;
};

export type PromiseRow = {
  id: string;
  symbol: string;
  metric: string;
  target_value: string | null;
  target_period: string | null;
  quote: string;
  said_on: string | null;
  basis: string | null;
  period_type: string | null;
  status: "open" | "kept" | "missed" | "dropped";
  actual_value: string | null;
  comparison: string | null;
  scoring_note: string | null;
  source_period?: string | null;
  source_url?: string | null;
};

export type RelatedFiling = {
  id: string;
  title: string;
  pdf_url: string | null;
  published_at: string;
  materiality: number;
};

export type DigestItem = {
  id: string;
  symbol: string;
  name: string;
  published_at: Date;
  title: string;
  category: string;
  pdf_url: string | null;
  materiality: number;
  score_parts: ScoreParts;
  summary: string | null;
  extracted: Extracted | null;
  related_count: number;
  related: RelatedFiling[];
  due_promises: PromiseRow[];
};

export type GuidanceQuote = { quote: string; topic: string };

export type AnalysisPayload = {
  snapshot: string;
  how_it_makes_money: string;
  segments?: { name: string; note: string }[];
  financial_read: string;
  bull: string[];
  bear: string[];
  // Set only on rows written by the live research route.
  source?: "live";
  tier?: "cheap";
  guidance?: GuidanceQuote[];
  guidance_rejected?: number;
  transcript_url?: string;
  transcript_period?: string;
  statement_basis?: "consolidated" | "standalone";
};

export type Analysis = {
  symbol: string;
  payload: AnalysisPayload;
  model_used: string | null;
  degraded: boolean;
  built_at: Date;
};

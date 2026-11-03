// The 17 glossary categories, their materiality weights and display labels.
// Weights mirror ingest/materiality.py; the score itself is always read from score_parts.

export const CATEGORY_WEIGHT: Record<string, number> = {
  results: 55, "M&A": 55, management_change: 55, fund_raise: 55, litigation: 55,
  capex: 35, credit_rating: 35, dividend: 35, bonus_split: 35, pledging: 35,
  earnings_call: 25, insider_trading: 25, press_release: 25,
  agm_egm: 12, postal_ballot: 12, investor_meetings: 12,
  unclassified: 3,
};

export const CATEGORIES: string[] = Object.keys(CATEGORY_WEIGHT);

const LABELS: Record<string, string> = {
  results: "Results", "M&A": "M&A", management_change: "Mgmt change",
  fund_raise: "Fund raise", litigation: "Litigation", capex: "Capex",
  credit_rating: "Credit rating", dividend: "Dividend", bonus_split: "Bonus/split",
  pledging: "Pledging", earnings_call: "Earnings call", insider_trading: "Insider",
  press_release: "Press release", agm_egm: "AGM/EGM", postal_ballot: "Postal ballot",
  investor_meetings: "Investor meet", unclassified: "Routine",
};

export function categoryLabel(category: string): string {
  return LABELS[category] ?? category;
}

export const CARD_THRESHOLD = 60;
export const ROW_THRESHOLD = 30;

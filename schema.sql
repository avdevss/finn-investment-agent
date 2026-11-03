-- FINN schema. Idempotent: applied by `python -m ingest.db --apply-schema`.

create table if not exists companies (
  symbol        text primary key,          -- NSE symbol, e.g. RELIANCE
  name          text not null,
  bse_scrip     text,
  screener_slug text,                      -- usually the NSE symbol, not guaranteed
  sector        text,
  in_portfolio  boolean not null default true
);

create table if not exists prices (
  symbol       text not null references companies(symbol) on delete cascade,
  date         date not null,
  open         numeric, high numeric, low numeric, close numeric,
  volume       bigint,
  pct_change   numeric,                    -- versus previous close, percent
  vol_20d_avg  numeric,
  vol_ratio    numeric,                    -- volume / vol_20d_avg
  primary key (symbol, date)
);
create index if not exists prices_symbol_date_desc on prices (symbol, date desc);

create table if not exists announcements (
  id            text primary key,          -- sha256(symbol|bse_newsid), else sha256(symbol|published_at|title)
  symbol        text not null references companies(symbol) on delete cascade,
  bse_newsid    text,
  published_at  timestamptz not null,
  title         text not null,
  category      text not null,             -- one of the 17 glossary categories
  bse_category  text,                      -- BSE's own CATEGORYNAME, kept as evidence
  pdf_url       text,
  materiality   int not null,              -- 0 to 100
  score_parts   jsonb not null,            -- breakdown, incl. price_date and price_basis
  summary       text,                      -- one line, only for material items
  extracted     jsonb,                     -- results numbers, if category = results
  classified_by text not null,             -- rules | bse_category | ambiguous_title | pdf_text:<model>
  pdf_sha256    text                       -- the exact bytes a pdf_text re-read was based on
);
create index if not exists announcements_window on announcements (published_at desc, materiality desc);
create index if not exists announcements_symbol on announcements (symbol, published_at desc);
create unique index if not exists announcements_newsid on announcements (bse_newsid)
  where bse_newsid is not null;

create table if not exists documents (
  id          text primary key,            -- sha256 of the PDF bytes
  symbol      text not null references companies(symbol) on delete cascade,
  doc_type    text not null,               -- annual_report | concall | results
  period      text,                        -- e.g. FY25, Q2FY26
  url         text,
  text_path   text,                        -- local path to extracted text
  pages       int,
  char_count  int,                         -- 0 means a scanned PDF; skip, do not OCR
  doc_date    date,                        -- when the call happened, for promises.said_on
  speaker_mode text,                       -- labelled | unsegmented, honest about coverage
  mgmt_chars  int,                         -- characters attributed to management
  promises_built_at timestamptz,           -- extraction ran, even if it found nothing
  pass1_rejected int,                      -- sentences that were not verbatim in the chunk
  pass2_rejected int,                      -- quotes that were not verbatim in the document
  fetched_at  timestamptz not null default now()
);
create index if not exists documents_symbol_type on documents (symbol, doc_type, period);

create table if not exists promises (
  id            text primary key,
  symbol        text not null references companies(symbol) on delete cascade,
  source_doc    text references documents(id) on delete set null,
  metric        text not null,             -- revenue_growth | ebitda_margin | capex | other
  target_value  text,                      -- text, guidance is often a range
  target_period text,                      -- FY26, Q4FY26
  quote         text not null,             -- verbatim, this is the evidence
  said_on       date,
  basis         text,                      -- reported | constant_currency | segment | unclear
  period_type   text,                      -- quarter | year, as the model read the deadline
  status        text not null default 'open',  -- open | kept | missed | dropped
  actual_value  text,
  checked_at    timestamptz,
  comparison    text,                      -- floor | ceiling, how target_value was read
  scoring_note  text,                      -- why a promise is still open
  model_used    text               -- the model that structured this row
);
create index if not exists promises_symbol_status on promises (symbol, status);
create index if not exists promises_due on promises (target_period, status);

-- Actuals the Promise Ledger is scored against. announcements.extracted holds only
-- the one latest quarter per company, which cannot judge a promise made a year ago.
create table if not exists quarterly_results (
  symbol           text not null references companies(symbol) on delete cascade,
  period_end       date not null,          -- quarter end, e.g. 2026-09-30
  period           text not null,          -- Q2FY27
  sales            numeric,
  operating_profit numeric,
  opm              numeric,                -- percent
  other_income     numeric,
  interest         numeric,
  depreciation     numeric,
  pbt              numeric,
  net_profit       numeric,
  source           text not null,          -- screener_quarters
  primary key (symbol, period_end)
);
create index if not exists quarterly_results_period on quarterly_results (period);

-- Annual actuals, off the same screener page. A target for FY27 and a target for
-- Q4FY27 both end on 31 March 2027, so a full year commitment scored against the
-- March quarter would be judged against a quarter of its own number.
create table if not exists annual_results (
  symbol           text not null references companies(symbol) on delete cascade,
  period_end       date not null,          -- year end, 31 Mar
  period           text not null,          -- FY26
  sales            numeric,
  operating_profit numeric,
  opm              numeric,                -- percent
  other_income     numeric,
  interest         numeric,
  depreciation     numeric,
  pbt              numeric,
  net_profit       numeric,
  source           text not null,          -- screener_annual
  primary key (symbol, period_end)
);

create table if not exists analysis (
  symbol         text primary key references companies(symbol) on delete cascade,
  payload        jsonb not null,           -- business snapshot, financials, bull, bear
  model_used     text,
  degraded       boolean not null default false,
  prompt_tokens  int not null default 0,
  output_tokens  int not null default 0,
  built_at       timestamptz not null default now()
);

create table if not exists alert_rules (
  id          serial primary key,
  kind        text not null,               -- price_move | volume_spike | category
  threshold   numeric,
  categories  text[],
  enabled     boolean not null default true,
  updated_at  timestamptz not null default now()
);
-- One rule per kind, matching the UI: one price slider, one volume slider,
-- one category multi select. Makes the seed below idempotent.
create unique index if not exists alert_rules_kind on alert_rules (kind);

insert into alert_rules (kind, threshold, categories) values
  ('price_move',   5.0,  null),
  ('volume_spike', 2.0,  null),
  ('category',     null, array['results','M&A','management_change','fund_raise','litigation'])
on conflict (kind) do nothing;

create table if not exists alerts (
  id        text primary key,              -- sha256(symbol|rule kind|day it fired)
  symbol    text not null references companies(symbol) on delete cascade,
  rule_id   int not null references alert_rules(id) on delete cascade,
  fired_at  timestamptz not null,
  payload   jsonb not null                 -- which rule, and by how much
);
create index if not exists alerts_fired_desc on alerts (fired_at desc);

-- Columns added after the tables first existed. `create table if not exists` skips a
-- table it already finds, so new columns need saying again here for the schema to
-- stay idempotent and still migrate.
alter table announcements add column if not exists pdf_sha256   text;
alter table documents     add column if not exists speaker_mode text;
alter table documents     add column if not exists doc_date     date;
-- Set once a transcript has been through promise extraction, whether or not it
-- yielded anything. Without it a transcript with no promises is re-read every run.
alter table documents     add column if not exists promises_built_at timestamptz;
-- Kept per transcript, so the verbatim rejection count survives the run that found it.
alter table documents     add column if not exists pass1_rejected int;
alter table documents     add column if not exists pass2_rejected int;
alter table documents     add column if not exists mgmt_chars   int;
alter table promises      add column if not exists comparison   text;
alter table promises      add column if not exists scoring_note text;
-- The basis guard. A target guided in constant currency or for one segment is not
-- comparable with a reported consolidated actual, and neither is an annual target
-- against a quarter, so both are recorded and the scorer refuses without them.
alter table promises      add column if not exists basis        text;
alter table promises      add column if not exists period_type  text;
-- Both promise passes run on the cheap tier now, so nothing can degrade here and a
-- column that is always false would only mislead. model_used carries the fact.
alter table promises      drop column if exists degraded;

-- No policies. DATABASE_URL connects as postgres (rolbypassrls), so ingest and the
-- server components are unaffected; the anon and authenticated roles behind the
-- PostgREST API get nothing.
alter table companies     enable row level security;
alter table prices        enable row level security;
alter table announcements enable row level security;
alter table quarterly_results enable row level security;
alter table annual_results    enable row level security;
alter table documents     enable row level security;
alter table promises      enable row level security;
alter table analysis      enable row level security;
alter table alert_rules   enable row level security;
alter table alerts        enable row level security;

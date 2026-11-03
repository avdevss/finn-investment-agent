# FINN

FINN is a mobile-first research agent for Indian listed companies. It reads every corporate filing a portfolio produces, decides which ones matter, and keeps a ledger of what management promised on earnings calls against what the company actually reported.

**Live app:** https://web-pink-rho-96.vercel.app
**Repo:** https://github.com/avdevss/finn-investment-agent

It is a triage queue for filings, not a dashboard. The default screen answers one question: of everything my 18 companies filed in the last 24 hours, what do I need to read? Every number on screen can be traced back to its source:
- tap a materiality score to see its arithmetic
- tap a filing to open the exchange PDF
- tap a promise to see management's words, verbatim, with a link to the transcript

## Screens

| Route | What it does |
|---|---|
| `/` Digest | Filings in a 24h / 3d / 7d / 30d window, sorted by materiality. Scores of 60 and above get a full card, 30-59 a compact row, and everything below collapses into one grey "N routine filings" line. Filings with the same company and category are grouped as "+N related". Results cards show Revenue, EBITDA, PBT and PAT with QoQ and YoY, plus any promises that came due that quarter. |
| `/alerts` | Price move, volume spike and filing category rules, each with a real control and an on/off toggle, saved to Postgres. The feed below lists everything that tripped, newest first, and states which rule fired and by how much. |
| `/company/[symbol]` | One year of price and volume, headline quarterly numbers, the Promise Ledger grouped by verdict, and a business snapshot with bull and bear cases. |
| `/research` | Any NSE symbol. Cached companies load instantly. An unknown ticker runs a live pipeline with staged progress: finding documents, extracting, analysing. |

## Architecture: ingest and serve are separate

```
  ingest/  (Python, runs on a laptop)
      | writes
      v
  Supabase Postgres
      ^ reads
      |
  web/  (Next.js on Vercel)
```

All scraping happens in `ingest/`, from a residential connection, on my own schedule. The deployed app reads the database and never scrapes a live source for a portfolio company.

**Why:** NSE blocks datacenter IPs, and its robots.txt disallows `/api/`. Yahoo rate limits without warning and returns an empty frame instead of an error. If the deployed app scraped live and a source refused during an evaluation, the demo would be dead. Splitting ingest from serve removes that failure mode entirely.

The one live path is `/research` for a ticker outside the database. It runs in a Vercel route handler, shows staged progress, and ends every failure with a clear message (see Known limits).

## Data sources

| Source | Used for | Notes |
|---|---|---|
| Yahoo Finance (`yfinance`) | 1 year of daily prices | Fetched in batches of 5 with `threads=False`. `pct_change`, `vol_20d_avg` and `vol_ratio` are computed at ingest and stored, so alerts and scores never recompute on page load. An empty frame is a hard error: a company with no prices would silently zero the price and volume parts of every score it has. |
| BSE announcements API | 30 days of corporate filings | Correction below. |
| BSE filing PDFs | Results statements, reclassification, summaries | Text extracted with pdfplumber. |
| screener.in | Concall transcript links, quarterly and annual actuals | One cached fetch per company, 1.5s between requests. The search endpoint is never used, because robots.txt disallows it. |

**The BSE endpoint correction.** The commonly cited `AnnGetData/w` endpoint is retired. It answers HTTP 200 with the bare string `"No Record Found!"`: no exception, no empty list. Read naively, that looks exactly like a company with no filings. The live endpoint is `AnnSubCategoryGetData/w`, and it needs four things:
- **A current browser User-Agent.** Akamai returns 403 for `Chrome/120` and passes `Chrome/153`.
- **Per-company requests.** An empty `strScrip` returns zero rows, not the whole market.
- **Paging.** `Table1[0].ROWCNT` is the total for the window, and a page holds 50 rows. Measured: RELIANCE over 180 days has 96 rows across two pages, so taking page one alone would silently drop the older half.
- **Deduplication on `NEWSID`**, which is unique per filing, across overlapping runs.

`TATAMOTORS` was dropped from the portfolio because the symbol was renamed `TMPV` in October 2025, and Yahoo now returns an empty frame for it. `BAJFINANCE` replaced it.

## Classification and materiality

**Category, cheapest step first.** Each filing gets one of 17 categories:
1. Regex rules on the title, with routine compliance patterns matched first. Otherwise "Appointment of Statutory Auditors" reads as a management change.
2. BSE's own subcategory.
3. An ambiguity test that strips citations, meeting furniture and dates. Titles with nothing left are held as ambiguous instead of being guessed by a model.
4. The cheap model, batched, for titles that match nothing.
5. A re-read of the first page of the filing PDF for every row the title could not settle.

Measured on the 30-day window: 223 settled by rules, 17 by BSE's subcategory, 16 by the model from the title, 15 re-read from the PDF and 25 held as ambiguous.

**Materiality is arithmetic, not a model.** A score from 0 to 100, with the breakdown stored and shown on tap:

```
category weight (3 / 12 / 25 / 35 / 55)
+ min(25, |price change that day| × 4)
+ min(15, (volume ratio − 1) × 10)
```

Two refinements:
- **Trading-day mapping.** A filing published at 23:05 is scored against the next trading day's move, not the one that had already closed.
- **The notice cap.** A results "intimation" whose PDF holds no P&L is capped at 25. It is a calendar entry, not the numbers.

**Results extraction is deterministic.** `pdfplumber`'s table extraction merges columns on BSE PDFs, so the text lines are parsed directly, with OCR repairs. The three column headers are verified as quarter, prior quarter and year-ago quarter before any number is trusted.

EBITDA is not a statutory line, so it is derived as PBT + finance costs + depreciation − other income, and that formula is stored with the number. For RELIANCE Q2 FY27 this gives 56,616, which matches screener's operating profit to the rupee. Lenders report total income, PBT and PAT, with EBITDA marked "not applicable".

## The Promise Ledger

Management makes quantified commitments on every earnings call: growth targets, margin bands, capex plans. Almost nobody checks them later. FINN extracts each commitment as a structured record with its deadline, and scores it against the reported number once that period closes.

1. **Four transcripts per company** from screener.in. With only the latest call, every promise is still open and the track record is empty.
2. **Speaker attribution** removes analysts and the moderator before anything is extracted. Four signals are combined because transcript layouts differ:
   - the participants block
   - the moderator's "next question is from…" announcements, the only signal present in all 80 transcripts
   - whoever holds the floor before Q&A starts
   - prose introductions

   Every company lands between 49% and 100% of its text attributed to management. This works where the transcript layout allows it: 78 of 80 transcripts were segmented by speaker, 2 were not and are recorded as `unsegmented` rather than claimed as filtered, and ICICIBANK is only partly separated. The bias is deliberate: an unrecognised voice is treated as an analyst, because losing a commitment is better than inventing one.
3. **A deterministic gate.** A 20,000-character chunk with no digit and no forward-looking verb never reaches a model.
4. **Pass 1, gemini-3.5-flash-lite:** candidate sentences, copied exactly.
5. **Verbatim check, in code:** each returned sentence must be a whitespace-normalised substring of its own chunk. Measured: **88 invented or altered sentences rejected**. On RELIANCE, the model invented two management commitments that appear nowhere in the document.
6. **Pass 2:** the surviving sentences go in as a numbered list. The model returns `sentence_index`, metric, target, period, period type and basis, **never the quote**. The quote is looked up by index, in code, so the evidence can never be a paraphrase. The call's fiscal context is computed and passed in, so that "this year" resolves correctly.
7. **Second verbatim check** against the whole document before insert. Measured: 0 rejections.
8. **The basis guard.** A verdict is issued only when the comparison is sound: a company level basis, with a quarterly target against a quarterly actual or an annual target against the annual P&L. A target guided in constant currency or for one segment stays open with the reason shown, for example "guided in constant currency, actual is reported".
9. **Scoring is arithmetic**, against up to 13 quarters and 12 years of screener actuals. A range is kept at or above its low end, and a single value is a floor unless the wording makes it a ceiling. Every assumption the scorer makes is written into the promise's `scoring_note`, next to the verdict it led to. The rules, in order:
   - **Cleanup before scoring.** A promise whose quote ends in a question mark is an analyst asking, not management committing, and it is deleted (3 removed). Restatements of one commitment are deleted too, keeping the earliest call's quote, so a target repeated on three calls weighs once. Duplicates share the same symbol, metric, period and target value (27 removed). Promises with no period are never treated as duplicates.
   - **The call date sets "this year".** When the quote says "this year", "current year", "this fiscal" or "current fiscal", the call date fixes the fiscal year. April to December belongs to the year ending the next March; January to March to the year ending that March. This overrides the model's year label, because on April calls the model read "this year" as the year just closed. A quarter or half label named in the record stands. Labels like `FY2030` are normalised to `FY30`.
   - **Period sanity.** A target period that ended before the call was held cannot be a forward commitment for it, so the promise stays open as "period ended before the call, likely mislabelled".
   - **Unclear basis fallback.** When the model called the basis unclear and the quote contains no segment, vertical, division, export, region or constant currency wording, the target is treated as company level: "basis assumed reported: no segment wording in quote".
   - **Qualitative floors**, for percentage metrics: "double digit" means at least 10%, "high single digit" at least 7%. For growth only, "double" means at least 100% and "3x" at least 200%.
   - **Magnitude check**, overriding everything above, whatever basis was recorded. A money target under a fifth of the reported figure, or a margin target more than 3 points from the reported margin, goes back to open as "target far from company level actual, likely a segment target". A money target more than 3x away in either direction is refused as a different scope or unit. This is what returned two DIXON targets of ₹5,200 and ₹5,500 crore to open; they had been marked kept against revenue of ₹48,873 crore. It also returned LT's 8.5% margin target, set against a 13% reported margin.
   - **The quote must name the metric.** A verdict is issued only if the quote itself says revenue, sales, growth, margin, EBITDA, profit or PAT. "We'll be able to achieve 15%" stays open as "quote does not name the metric", whatever the model labelled it.

   Each of these rules removed at least one wrong verdict that a looser scorer had issued with a verbatim quote attached.

**Where the ledger stands.**
- **4 verdicts scored:** 1 kept and 3 missed. ADANIPORTS kept its ₹22,800 crore FY26 EBITDA guide. ASTRAL, KPITTECH and LT missed FY26 growth or margin guides.
- **190 promises open**, each with its reason stored.
- **Removed before scoring:** 3 analyst questions and 27 duplicates.

17 of the 22 ingested companies have a ledger. The five without one:
- **ITC:** no concall transcripts on screener.in.
- **HDFCBANK, ICICIBANK, TITAN and CDSL:** their transcripts yielded no quantified forward commitment that passed the checks.

**The guarantee:** every quote in the ledger is a character-for-character substring of the source transcript, checked twice in code, and was never written by the model that structured it.

## Model routing

Gemini, with model names read from `.env`. Preview models are refused in code. The free tier allows **20 requests per day per model**: an exhausted allowance returns 429 with a `RetryInfo` of about 17 hours, and the client treats any advised wait over 300 seconds as a quota wall rather than congestion.

| Tier | Used for | Model |
|---|---|---|
| 0 | Category rules, materiality, alert evaluation, promise scoring | None: rules and arithmetic |
| 1 | Results tables from filing PDFs | pdfplumber text lines; a model only if parsing fails |
| 2 | Title classification, PDF reclassification, one-line summaries, both promise passes, live research | `CHEAP_MODEL` = gemini-3.5-flash-lite |
| 3 | Business snapshot and bull/bear | designed for `STRONG_MODEL` = gemini-3.6-flash; in practice gemini-3.5-flash-lite |

**What actually ran.** Tier 3 was designed for gemini-3.6-flash, but the free tier limit of 20 requests per day made it unusable at portfolio scale. So **23 of 24 analysis rows and all 194 promises came from gemini-3.5-flash-lite**. Only the RELIANCE analysis came from gemini-3.6-flash. Every row records `model_used`, and the company page shows it next to the analysis. On a paid tier, tier 3 moves back to gemini-3.6-flash with a one-line `.env` change.

All model calls in `ingest/` go through one client (`ingest/llm.py`). It provides:
- a global throttle
- patient retries on 429, 5xx and read timeouts
- a two-strike circuit breaker per model
- a disk cache keyed on the sha256 of the full request, model name included

A rerun does no work for anything already answered.

## Measured cost

Counted from the model client's disk cache (one entry per answered call) and the `analysis` table, which records live research runs.

| Model | Calls | Input tokens | Output tokens |
|---|---:|---:|---:|
| gemini-3.5-flash-lite, ingest | 427 | 1,307,349 | 73,624 |
| gemini-3.5-flash-lite, live research (PERSISTENT, HAVELLS) | 4 | 51,258 | 1,648 |
| gemini-3.6-flash, ingest (RELIANCE analysis) | 2 | 16,124 | 1,922 |
| **Total** | **433** | **1,374,731** | **77,194** |

The ingest flash-lite row includes 15 calls that requested tier 3 and were answered by flash-lite, the model each of those rows records.

240 of 296 filings (81%) were classified by rules and the exchange subcategory with no model. The cheap model handled 16, 15 were re-read from the PDF, and 25 vague titles were held at low weight instead of guessed.

**Actual spend: ₹0.** Everything ran on the Gemini free tier.

**Equivalent paid cost (estimate):** about **$0.62, or ₹58**, for the same tokens at list price:
- gemini-3.5-flash-lite: $0.30 in / $2.50 out per 1M tokens
- gemini-3.6-flash: $0.75 / $3.75 per 1M tokens (until 31 Dec 2026)
- prices read from Google's pricing page on 3 Nov 2026, at ₹94.73 per USD

## Running it

**Requirements:** Python 3.11, Node 22, and a Supabase project. Copy `.env.example` to `.env` and `web/.env.example` to `web/.env.local`, then fill in the values.

```bash
# ingest
python -m venv .venv && .venv/bin/pip install -r requirements.txt
python -m ingest.db --apply-schema
python -m ingest.seed_companies
python -m ingest.prices
python -m ingest.announcements --days 30
python -m ingest.filings          # results extraction, PDF reclassification, summaries
python -m ingest.documents        # concall transcripts
python -m ingest.quarters         # quarterly and annual actuals
python -m ingest.promises         # extraction and scoring
python -m ingest.analysis         # snapshot, bull, bear
python -m ingest.db --counts      # what is in the database

# web
cd web && npm install && npm run dev
```

**Vercel** needs three environment variables:
- `DATABASE_URL`, the Supabase transaction pooler string
- `GEMINI_API_KEY`
- `CHEAP_MODEL`

## Known limits

- **VBL has a December year end.** Annual actuals assume an April-to-March fiscal year, so VBL's December columns are dropped rather than mislabelled. Its annual promises stay open.
- **ITC has no concall transcripts on screener.in.** So it has no ledger, and its analysis rests on the quarterly numbers table only.
- **Four more companies have no ledger.** HDFCBANK, ICICIBANK, TITAN and CDSL yielded no quantified forward commitment that passed the checks.
- **Call dates are months.** Screener labels each concall by month only, so a transcript's stored date is that month's last day. The app shows it as a month, and a promise's "said on" is accurate to the month, not the day.
- **Alert rules are global**, one set for the whole app. There are no user accounts.
- **Live research runs on the cheap tier**, capped at 10 successful runs per day across all users:
  - It does not port speaker attribution, so guidance quotes are verbatim but not verified as management's words, and the screen says so.
  - The cap counts successful runs only.
  - A live ticker has no price history.
- **Annual reports are not ingested.** Only transcripts, filings and screener actuals are used.
- **Management change bio enrichment is not built.** Those cards show the filing summary only.
- **Most promises are still open.** Most deadlines are FY27 or later, and the guards refuse comparisons that only look sound. That is deliberate: a missing verdict is better than a wrong one.

## What I would build next

- Annual report ingestion, for multi-year trajectory and cash-flow quality.
- Promise scoring over a five-year window, giving each management team a real credibility score.
- Segment and constant-currency actuals, so the guarded promises can be scored as well.
- Real push notifications when an alert rule trips.
- Speaker attribution ported to the live research path.
- A `live_runs` table, so the daily cap counts failed runs too.

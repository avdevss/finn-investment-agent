# The Promise Ledger: quantified commitments management made on an earnings call,
# extracted with the deadline attached, then scored against what was actually
# reported once that period closed.
#
# Two model passes with a deterministic gate in front and a verbatim check behind:
#
#   gate    a chunk with no number and no forward looking verb never reaches a model
#   pass 1  cheap model returns candidate sentences, copied exactly
#   check   every sentence must be a substring of its chunk, or it is rejected
#   pass 2  cheap model structures the survivors and refers to them BY INDEX
#   check   every quote must be a substring of the source document, or it is rejected
#   score   no model at all, arithmetic against the reported actuals
#
# Pass 2 never returns the quote. The quote is looked up from the pass 1 list in
# code, so the evidence shown in the UI cannot be a regenerated paraphrase.
#
# Both passes run on the cheap tier. The free tier allows 20 requests per day per
# model and the ledger needs one pass 2 call per transcript, so the strong tier is
# reserved for the one analysis call per company that cannot be done any other way.
#
# Pass 2 also reports the basis of the target and whether its deadline is a quarter
# or a year, and the scorer refuses a verdict unless both line up with the actual it
# would compare against. A constant currency target against a reported actual, or a
# full year target against the March quarter, is a comparison that looks rigorous and
# is not. Those stay open, carrying the reason.

from __future__ import annotations

import argparse
import hashlib
import logging
import pathlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Final

import psycopg

from ingest import periods, speakers
from ingest.db import connect, upsert
from ingest.llm import LLMError, generate_json
from ingest.seed_companies import COMPANIES

log = logging.getLogger(__name__)

# Large chunks on purpose. The cheap model reads far more than this in one call, and
# a concall transcript cut into 2,000 token pieces costs five times as many requests
# for the same answer. Splitting on sentence boundaries means no overlap is needed
# and no sentence is ever cut in half.
CHUNK_CHARS: Final = 20_000

METRICS: Final[tuple[str, ...]] = (
    "revenue", "revenue_growth", "ebitda", "ebitda_margin", "pbt", "pat",
    "capex", "margin", "volume", "other",
)

MONEY: Final = "money"
PERCENT: Final = "percent"

# Which reported line answers a metric, whether the actual is a level or a year on
# year growth rate, and whether it is an amount or a rate. Anything absent here
# cannot be scored and says so.
_ACTUAL_COLUMN: Final[dict[str, tuple[str, str, str]]] = {
    "revenue": ("sales", "level", MONEY),
    "revenue_growth": ("sales", "growth", PERCENT),
    "ebitda": ("operating_profit", "level", MONEY),
    "ebitda_margin": ("opm", "level", PERCENT),
    "margin": ("opm", "level", PERCENT),
    "pbt": ("pbt", "level", MONEY),
    "pat": ("net_profit", "level", MONEY),
}

# Screener reports in rupee crore, so a money target is converted to crore before it
# is compared. Management says "INR 12 billion" as readily as "INR 1,200 crore", and
# comparing 12 against a crore figure is wrong by two orders of magnitude.
_UNITS: Final[dict[str, float]] = {
    "lakh": 0.01, "crore": 1.0, "cr": 1.0, "million": 0.1, "mn": 0.1,
    "billion": 100.0, "bn": 100.0, "trillion": 100_000.0,
}
_UNIT_WORD: Final = re.compile(
    r"\b(lakhs?|crores?|cr|millions?|mn|billions?|bn|trillions?)\b", re.I)

# How far apart an amount target and the reported amount may be before the comparison
# is refused. Nobody guides a figure three times what they report on the same basis:
# such a gap means the target was for one division, or in another unit. Measured, this
# is the gap that let two DIXON segment targets of INR 5,200 and 5,500 crore be marked
# kept against FY26 revenue of INR 48,873 crore, with the quote attached as evidence.
SCALE_TOLERANCE: Final = 3.0

# A money target under a fifth of the reported figure, or a margin more than 3 points
# from the reported margin, describes part of the company, whatever basis the model
# recorded. Neither may earn a verdict.
SEGMENT_RATIO: Final = 5.0
MARGIN_GAP_PP: Final = 3.0
SEGMENT_NOTE: Final = "target far from company level actual, likely a segment target"

# Wording that scopes a figure to part of the company or adjusts it for currency. A
# quote with none of it and a basis the model called unclear is read as company level.
_SCOPE_WORDS: Final = re.compile(
    r"\b(segments?|verticals?|divisions?|exports?|regions?|regional|"
    r"constant currency|cc)\b", re.I)
ASSUMED_REPORTED: Final = "basis assumed reported: no segment wording in quote"

# Relative deadlines that can only mean the fiscal year the call was held in.
_THIS_YEAR: Final = re.compile(
    r"\b(this year|current year|this fiscal|current fiscal|this financial year|"
    r"current financial year)\b", re.I)

# A verdict needs the quote itself to say what is being measured. "We'll be able to
# achieve 15%" could be growth, margin or market share; the model's label is a guess.
_METRIC_WORDS: Final = re.compile(
    r"\b(revenues?|sales|grow(?:th|s|ing)?|margins?|ebitda|profits?|pat)\b", re.I)
NO_METRIC_NOTE: Final = "quote does not name the metric"
MISLABELLED_NOTE: Final = "period ended before the call, likely mislabelled"

# "3x" or "2 times": a growth target stated as a multiple of the base year.
_MULTIPLE: Final = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:x|times)\b", re.I)

# Words used in place of a number, and the floor each one guarantees. The digit
# phrases go first, or "double-digit" would read as "double". The flag marks the one
# that only means something for a growth rate.
_QUALITATIVE: Final[tuple[tuple[re.Pattern[str], float, str, bool], ...]] = (
    (re.compile(r"high single[- ]digits?", re.I), 7.0, "high single digit", False),
    (re.compile(r"double[- ]digits?", re.I), 10.0, "double digit", False),
    (re.compile(r"\bdoubl(?:e|ing)\b", re.I), 100.0, "double", True),
)

_HAS_DIGIT: Final = re.compile(r"\d")
_FORWARD: Final = re.compile(
    r"\b(expect|expects|expecting|guidance|guide|target|targets|targeting|aim|aims|"
    r"aiming|plan|plans|planning|intend|intends|project|projects|projected|forecast|"
    r"outlook|anticipate|anticipates|commit|commits|committed|on track|trajectory|"
    r"will be|will reach|will grow|should be|should reach|going forward|by fy|"
    r"next year|next quarter|coming year|coming quarter|over the next|"
    r"in the next|end of the year|run rate)\b", re.I)

_SENTENCE_SPLIT: Final = re.compile(r"(?<=[.!?])\s+")

STATUS_OPEN: Final = "open"
STATUS_KEPT: Final = "kept"
STATUS_MISSED: Final = "missed"

# The basis a target was guided on. Only `reported` is comparable with the reported
# consolidated actuals this project stores, so only `reported` can earn a verdict.
BASIS_REPORTED: Final = "reported"
BASES: Final[tuple[str, ...]] = (BASIS_REPORTED, "constant_currency", "segment",
                                 "unclear")
PERIOD_TYPES: Final[tuple[str, ...]] = (periods.QUARTER, periods.YEAR)

_BASIS_NOTE: Final[dict[str, str]] = {
    "constant_currency": "guided in constant currency, actual is reported",
    "segment": "guided for one segment, actual is the consolidated reported figure",
    "unclear": "the basis of the target is unclear, actual is reported",
}

FLOOR: Final = "floor"
CEILING: Final = "ceiling"
_CEILING_WORDS: Final = re.compile(
    r"\b(below|under|less than|lower than|at most|no more than|not exceed|within)\b", re.I)

_PASS1_SCHEMA: Final = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {"type": "string"}}},
    "required": ["sentences"],
}
_PASS1_SYSTEM: Final = (
    "You read transcripts of Indian listed company earnings calls and pull out "
    "forward looking quantified commitments made by company management.\n"
    "Rules, all of them strict:\n"
    "1. Use only the text given to you. Never use outside knowledge about the "
    "company and never infer a number the text does not state.\n"
    "2. Return only sentences spoken by company management or executives. Never "
    "return a sentence spoken by an analyst, a questioner, a moderator or an "
    "operator, and never return an analyst's question.\n"
    "3. Return only commitments about a future period: a target, a guidance figure, "
    "a plan or an expectation. Never return a past or current result, and never "
    "return a description of what already happened.\n"
    "4. The sentence must contain a specific number, percentage, range or amount. "
    "Skip vague statements with no figure.\n"
    "5. Copy each sentence EXACTLY as it appears, character for character. Do not "
    "fix grammar, do not shorten, do not join sentences, do not add quotation marks.\n"
    "Return an empty list when the text contains no such sentence, which is the "
    "normal case for most of a transcript."
)

_PASS2_SCHEMA: Final = {
    "type": "object",
    "properties": {
        "promises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence_index": {"type": "integer"},
                    "metric": {"type": "string", "enum": list(METRICS)},
                    "target_value": {"type": "string"},
                    "target_period": {"type": "string"},
                    "period_type": {"type": "string", "enum": list(PERIOD_TYPES)},
                    "basis": {"type": "string", "enum": list(BASES)},
                },
                "required": ["sentence_index", "metric", "target_value",
                             "target_period", "period_type", "basis"],
            },
        }
    },
    "required": ["promises"],
}
_PASS2_SYSTEM: Final = (
    "You turn sentences from an earnings call into structured records of management "
    "commitments. Use only the sentences given to you; never use outside knowledge "
    "and never invent a figure or a deadline that is not in the sentence.\n"
    "For each sentence that states a genuine forward looking quantified commitment, "
    "return one record with:\n"
    "- sentence_index: the number of the sentence you are describing\n"
    "- metric: what is being committed to, from the allowed list\n"
    "- target_value: the figure or range exactly as stated, e.g. '18-20%', "
    "'Rs 8,000 crore', 'double digit'\n"
    "- target_period: the deadline, normalised to one of QnFYnn, FYnn or HnFYnn, "
    "e.g. 'Q4FY27' or 'FY28'. The call context below tells you which quarter and "
    "fiscal year the speakers are in; resolve every relative deadline against it, so "
    "'this year' and 'the full year' mean the current fiscal year, 'next year' the "
    "one after, 'the second half' the current year's H2, and 'next quarter' the "
    "quarter after the one just reported. If the sentence names an absolute period "
    "such as FY2030, use that. If it gives no deadline at all, return an empty "
    "string.\n"
    "- period_type: 'quarter' if the deadline is a single quarter, 'year' if it is a "
    "full financial year. A half year deadline such as H2FY27 is a 'year' commitment "
    "only if the sentence says so; otherwise use the span the sentence states.\n"
    "- basis: how the figure was guided. 'reported' for a figure on the same basis as "
    "the reported consolidated accounts. 'constant_currency' if the sentence says "
    "constant currency, like for like, or excludes a currency effect. 'segment' if "
    "the figure is for one business, division, geography or product line rather than "
    "the whole company. 'unclear' if the sentence does not make the basis knowable. "
    "Do not guess 'reported' when the sentence is silent and the figure could be "
    "either; 'unclear' is the honest answer and costs nothing.\n"
    "Do NOT return the sentence text itself. Skip any sentence that is a past "
    "result, an analyst question, or has no number. Returning fewer records than "
    "sentences is expected and correct."
)


@dataclass
class Rejections:
    pass1: int = 0          # sentence was not verbatim in its chunk
    pass2: int = 0          # quote was not verbatim in the source document
    no_period: int = 0      # a commitment with no deadline cannot be scored
    question: int = 0       # the sentence was a question, so an analyst's, not a commitment
    failed: int = 0         # a model call never answered; the document stays unfinished


@dataclass
class Candidate:
    sentence: str
    chunk: int


def flatten(text: str) -> str:
    """Whitespace normalised, so a line break inside a PDF quote does not fail it."""
    return " ".join((text or "").split())


def chunk(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Sentence aligned chunks. No overlap, because no sentence is ever split."""
    out: list[str] = []
    current: list[str] = []
    length = 0

    for sentence in _SENTENCE_SPLIT.split(flatten(text)):
        if length + len(sentence) > size and current:
            out.append(" ".join(current))
            current, length = [], 0
        current.append(sentence)
        length += len(sentence) + 1

    if current:
        out.append(" ".join(current))
    return out


def worth_reading(body: str) -> bool:
    """The deterministic gate: no figure and no forward looking verb, no model call."""
    return bool(_HAS_DIGIT.search(body)) and bool(_FORWARD.search(body))


def candidates(chunks: list[str], rejected: Rejections) -> list[Candidate]:
    """Pass 1, plus the check that every sentence really came from the chunk."""
    found: list[Candidate] = []
    seen: set[str] = set()

    for index, body in enumerate(chunks):
        if not worth_reading(body):
            continue
        try:
            parsed, _ = generate_json(
                "Find the forward looking quantified commitments made by management "
                "in this transcript extract.\n\n--- transcript ---\n" + body,
                schema=_PASS1_SCHEMA, tier="cheap", system=_PASS1_SYSTEM,
                max_tokens=4096)
        except LLMError as e:
            # Not the same as a chunk with no guidance in it: this chunk was never
            # read. Counted, so the document is left for a rerun to finish.
            rejected.failed += 1
            log.error("pass 1 failed on chunk %d: %s", index, e)
            continue

        haystack = flatten(body)
        for sentence in parsed.get("sentences") or []:
            needle = flatten(sentence)
            if len(needle) < 25:
                continue
            if needle not in haystack:
                rejected.pass1 += 1
                log.warning("pass 1 rejected, not verbatim in chunk %d: %s",
                            index, needle[:90])
                continue
            if needle in seen:
                continue
            seen.add(needle)
            found.append(Candidate(needle, index))
    return found


def call_context(period: str, said_on: date | None) -> str:
    """What the speakers mean by "this year": computed, never left to the model."""
    period_end = periods.parse_label(period)
    if period_end is None:
        return "Call context: the reporting period is unknown."

    this_fy = periods.fy_label(period_end)
    next_fy = f"FY{(periods.fiscal_year(period_end) + 1) % 100:02d}"
    half = "H1" if period_end.month in (6, 9) else "H2"
    return (
        f"Call context, use this to resolve relative deadlines:\n"
        f"- the call was held on {said_on or 'an unstated date'} and reported {period}\n"
        f"- 'this year' / 'the full year' means {this_fy}\n"
        f"- 'next year' means {next_fy}\n"
        f"- 'the second half' means H2{this_fy}, and the call is in {half}{this_fy}\n"
        f"- 'next quarter' means {periods.label(periods.next_quarter_end(period_end))}"
    )


@dataclass
class Structured:
    records: list[dict[str, Any]]
    model: str = ""
    failed: bool = False    # the model never answered, as opposed to finding nothing


def structure(found: list[Candidate], period: str,
              said_on: date | None) -> Structured:
    """Pass 2. Records carry the sentence index only, never the quote."""
    if not found:
        return Structured([])

    listing = "\n".join(f"{i}. {c.sentence}" for i, c in enumerate(found))
    try:
        parsed, meta = generate_json(
            "Structure these management statements into commitment records.\n\n"
            + call_context(period, said_on)
            + "\n\n--- sentences ---\n" + listing,
            schema=_PASS2_SCHEMA, tier="cheap", system=_PASS2_SYSTEM,
            max_tokens=8192)
    except LLMError as e:
        log.error("pass 2 failed: %s", e)
        return Structured([], failed=True)

    records = []
    for item in parsed.get("promises") or []:
        index = int(item.get("sentence_index", -1))
        if 0 <= index < len(found) and item.get("metric") in METRICS:
            records.append({**item, "sentence_index": index})
    return Structured(records, meta.model_used)


def _ident(symbol: str, source_doc: str, quote: str, metric: str) -> str:
    key = f"{symbol}|{source_doc}|{metric}|{quote}"
    return hashlib.sha256(key.encode()).hexdigest()


def build(symbol: str, source_doc: str, said_on: date | None, source_text: str,
          found: list[Candidate], pass2: Structured,
          rejected: Rejections) -> list[dict[str, Any]]:
    """Promise rows, with the quote taken from our own sentence list and re-checked."""
    haystack = flatten(source_text)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for record in pass2.records:
        quote = found[record["sentence_index"]].sentence
        # Second check, against the whole document this time. The quote came from our
        # list rather than the model, so this should always pass; it is here so that
        # a future change which lets a model supply the text cannot slip past.
        if quote not in haystack:
            rejected.pass2 += 1
            log.warning("pass 2 rejected, quote not in source document: %s", quote[:90])
            continue

        if is_question(quote):
            rejected.question += 1
            continue

        period = (record.get("target_period") or "").strip()
        if not period:
            rejected.no_period += 1

        ident = _ident(symbol, source_doc, quote, record["metric"])
        if ident in seen:
            continue
        seen.add(ident)

        rows.append({
            "id": ident,
            "symbol": symbol,
            "source_doc": source_doc,
            "metric": record["metric"],
            "target_value": (record.get("target_value") or "").strip() or None,
            "target_period": periods.normalise_label(period) or None,
            "quote": quote,
            "said_on": said_on,
            "basis": record.get("basis") if record.get("basis") in BASES else None,
            "period_type": (record.get("period_type")
                            if record.get("period_type") in PERIOD_TYPES else None),
            "status": STATUS_OPEN,
            "actual_value": None,
            "checked_at": None,
            "comparison": None,
            "scoring_note": None,
            "model_used": pass2.model,
        })
    return rows


def extract_document(conn: psycopg.Connection, doc: dict[str, Any],
                     rejected: Rejections) -> int:
    """Run the whole chain for one transcript. Returns promises written."""
    path = pathlib.Path(doc["text_path"])
    if not path.is_absolute():
        path = pathlib.Path(__file__).parent.parent / path
    if not path.exists():
        log.warning("%s %s: extracted text missing at %s",
                    doc["symbol"], doc["period"], path)
        return 0

    attributed = speakers.attribute(path.read_text())
    body = attributed.management_text

    chunks = chunk(body)
    gated = sum(1 for c in chunks if worth_reading(c))

    mine = Rejections()
    found = candidates(chunks, mine)
    pass2 = structure(found, doc["period"], doc["doc_date"])
    rows = build(doc["symbol"], doc["id"], doc["doc_date"], body, found, pass2, mine)
    rejected.pass1 += mine.pass1
    rejected.pass2 += mine.pass2
    rejected.no_period += mine.no_period
    rejected.failed += mine.failed + int(pass2.failed)

    log.info("%-11s %-7s %2d chunks (%d gated in), %2d candidate sentences, "
             "%2d promises", doc["symbol"], doc["period"], len(chunks), gated,
             len(found), len(rows))

    if mine.failed or pass2.failed:
        # A model in the chain never answered. Whatever did answer is already in the
        # disk cache, so leaving this transcript unstamped costs a rerun the failed
        # calls only, and nothing is written from a half read document.
        log.warning("%-11s %-7s left unfinished: %d calls never answered",
                    doc["symbol"], doc["period"], mine.failed + int(pass2.failed))
        return 0

    written = upsert(conn, "promises", rows, ["id"]) if rows else 0
    # Marked done even when it found nothing, or a transcript with no quantified
    # guidance would be re-read on every run forever.
    with conn.cursor() as cur:
        cur.execute("""update documents set promises_built_at = %s,
                       pass1_rejected = %s, pass2_rejected = %s where id = %s""",
                    (datetime.now(timezone.utc), mine.pass1, mine.pass2, doc["id"]))
    conn.commit()
    return written


# ---------------------------------------------------------------- scoring, no model

# "18-20%", "12 billion to INR 16 billion". The unit and the currency may repeat in
# the middle of a range, and without allowing for that the high end is silently lost
# and the range collapses to its first number.
_RANGE: Final = re.compile(
    r"(-?\d[\d,]*\.?\d*)\s*(?:%|per cent|percent)?\s*"
    r"(?:lakhs?|crores?|cr|millions?|mn|billions?|bn|trillions?)?\s*"
    r"(?:to|-|–|—)\s*(?:inr|rs\.?|₹)?\s*(-?\d[\d,]*\.?\d*)", re.I)
_SINGLE: Final = re.compile(r"(-?\d[\d,]*\.?\d*)")


def parse_target(text: str) -> tuple[float, float] | None:
    """The numeric span a target states. '18-20%' -> (18, 20), '15%' -> (15, 15)."""
    body = (text or "").replace(",", "")
    m = _RANGE.search(body)
    if m:
        low, high = float(m.group(1)), float(m.group(2))
        return (low, high) if low <= high else (high, low)
    m = _SINGLE.search(body)
    if m:
        value = float(m.group(1))
        return value, value
    return None


def read_target(text: str, metric: str) -> tuple[tuple[float, float] | None, str | None]:
    """The span a target states, and how it was read when its words were not a number.

    "3x" is a multiple, not 3 percent, so it is read before the plain number parser
    can take it. A qualitative phrase becomes the floor it guarantees, and only for a
    metric where that floor is a percentage.
    """
    _, kind, unit = _ACTUAL_COLUMN.get(metric, ("", "", ""))
    growth = kind == "growth"

    multiple = _MULTIPLE.match(text or "")
    if multiple:
        if not growth:
            return None, None
        floor = (float(multiple.group(1)) - 1) * 100
        return (floor, floor), f"'{text}' read as growth of at least {floor:.0f}%"

    span = parse_target(text)
    if span is not None:
        return span, None

    for pattern, floor, name, growth_only in _QUALITATIVE:
        if pattern.search(text or "") and (growth if growth_only else unit == PERCENT):
            return (floor, floor), f"'{text}' read as {name}, at least {floor:.0f}%"
    return None, None


def is_question(quote: str) -> bool:
    return (quote or "").rstrip(" \"'”’").endswith("?")


def call_year(quote: str, said_on: date | None) -> tuple[str, str] | None:
    """(FY label, note) when the quote says "this year" and the call date fixes which.

    April to December belongs to the fiscal year ending the next March; January to
    March to the one ending that March. The call date wins over the model's label:
    on a Q4 call held in April, "this year" is the year just begun, and the model
    measured, labelled five such promises as the year just closed.
    """
    m = _THIS_YEAR.search(quote or "")
    if m is None or said_on is None:
        return None
    label = periods.fy_label(said_on)
    return label, f"period {label} set from '{m.group(1)}' on a call dated {said_on}"


def effective_period(target_period: str | None, quote: str,
                     said_on: date | None) -> tuple[str | None, str | None]:
    """The label a promise is scored against, and the note when it is not the model's.

    Only a full year label or a missing one is replaced. A quarter or half named in
    the record is more specific than "this year" and stands.
    """
    label = periods.normalise_label(target_period) if target_period else None
    parsed = periods.parse_period(label or "")
    if parsed is not None and parsed[1] != periods.YEAR:
        return label, None
    year = call_year(quote, said_on)
    if year is None:
        return label, None
    return year


def company_scale(metric: str, span: tuple[float, float], actual: float) -> str | None:
    """None when the target is plausibly a company level figure, else why not.

    Growth rates are not checked: a guide well below the actual growth is cautious
    guidance as often as it is a segment, and the verdict is the same either way.
    """
    _, kind, unit = _ACTUAL_COLUMN[metric]
    if unit == MONEY:
        if max(abs(span[0]), abs(span[1])) < abs(actual) / SEGMENT_RATIO:
            return SEGMENT_NOTE
        return comparable_scale(span, actual)
    if kind == "level":
        low, high = span
        gap = 0.0 if low <= actual <= high else min(abs(actual - low), abs(actual - high))
        if gap > MARGIN_GAP_PP:
            return SEGMENT_NOTE
    return None


def direction(text: str) -> str:
    """Whether a target is a floor to clear or a ceiling to stay under."""
    return CEILING if _CEILING_WORDS.search(text or "") else FLOOR


def unit_factor(text: str) -> float:
    """Multiplier taking a money target into crore. 1.0 when no unit is named."""
    m = _UNIT_WORD.search(text or "")
    if m is None:
        return 1.0
    word = m.group(1).lower().rstrip("s")
    return _UNITS.get(word, 1.0)


def in_crore(span: tuple[float, float], text: str) -> tuple[float, float]:
    factor = unit_factor(text)
    return (span[0] * factor, span[1] * factor)


def comparable_scale(span: tuple[float, float], actual: float) -> str | None:
    """None when an amount target and the reported amount are the same order of size.

    A model decides the basis, and a model can call a division's number consolidated.
    This is the arithmetic that catches it when it does.
    """
    biggest = max(abs(span[0]), abs(span[1]))
    if not biggest or not actual:
        return None
    ratio = max(abs(actual) / biggest, biggest / abs(actual))
    if ratio <= SCALE_TOLERANCE:
        return None
    return (f"target of {biggest:,.0f} crore is {ratio:.0f}x the reported "
            f"{abs(actual):,.0f} crore, so they are not the same scope")


Table = dict[date, dict[str, Any]]


@dataclass(frozen=True)
class Actuals:
    """The two sets of reported figures a target can be judged against."""
    quarterly: Table
    annual: Table

    def of(self, period_type: str) -> Table:
        return self.annual if period_type == periods.YEAR else self.quarterly


def _period_label(period_end: date, period_type: str) -> str:
    return (periods.fy_label if period_type == periods.YEAR else periods.label)(period_end)


def actual_for(metric: str, actuals: Actuals, period_end: date,
               period_type: str) -> tuple[float | None, str | None]:
    """The reported figure for a metric and period, or why there isn't one.

    The period type chooses the table, never the end date. FY27 and Q4FY27 both end
    on 31 March 2027, so judging a full year commitment against whichever row that
    date happened to find is how a target gets marked kept against a quarter of its
    own number.
    """
    mapping = _ACTUAL_COLUMN.get(metric)
    if mapping is None:
        return None, f"metric {metric} is not in the reported actuals"

    column, kind, _ = mapping
    table = actuals.of(period_type)
    label = _period_label(period_end, period_type)

    row = table.get(period_end)
    if row is None:
        if period_type == periods.YEAR and period_end in actuals.quarterly:
            return None, "annual target, quarter only"
        return None, f"period {label} has not closed"
    value = row.get(column)
    if value is None:
        return None, f"{column} not reported for {label}"

    if kind == "level":
        return float(value), None

    base = (table.get(periods.year_ago(period_end)) or {}).get(column)
    if base in (None, 0):
        return None, f"no year earlier {column} to measure growth against"
    return (float(value) - float(base)) / abs(float(base)) * 100, None


def comparable(basis: str | None, period_type: str | None, target_period: str,
               label_kind: str) -> str | None:
    """None when a verdict is allowed, else why the comparison would be unsound."""
    if basis != BASIS_REPORTED:
        return _BASIS_NOTE.get(basis or "", "the basis of the target was not recorded")
    if period_type not in PERIOD_TYPES:
        return "the period type of the target was not recorded"
    if label_kind == periods.HALF:
        return (f"{target_period} spans a half year, the actuals are quarterly "
                f"and annual")
    if label_kind != period_type:
        return (f"target period {target_period} names a {label_kind}, "
                f"the commitment was read as a {period_type}")
    return None


def verdict(span: tuple[float, float], actual: float, how: str) -> str:
    """At or above the low end is kept; below it is missed. A ceiling inverts that."""
    low, high = span
    if how == CEILING:
        return STATUS_KEPT if actual <= high else STATUS_MISSED
    return STATUS_KEPT if actual >= low else STATUS_MISSED


def _load_actuals(conn: psycopg.Connection,
                  symbols: list[str] | None) -> dict[str, Actuals]:
    where, params = ("where symbol = any(%s)", [symbols]) if symbols else ("", [])
    tables: dict[str, dict[str, Table]] = {}

    with conn.cursor() as cur:
        for name, table in (("quarterly_results", "quarterly"),
                            ("annual_results", "annual")):
            cur.execute(f"""select symbol, period_end, sales, operating_profit, opm,
                                   pbt, net_profit from {name} {where}""", params)
            for sym, pe, sales, op, opm, pbt, net in cur.fetchall():
                tables.setdefault(sym, {}).setdefault(table, {})[pe] = {
                    "sales": sales, "operating_profit": op, "opm": opm,
                    "pbt": pbt, "net_profit": net}

    return {sym: Actuals(t.get("quarterly", {}), t.get("annual", {}))
            for sym, t in tables.items()}


def score(conn: psycopg.Connection, symbols: list[str] | None) -> dict[str, int]:
    """Score every promise whose period has closed. Returns the status tally."""
    actuals = _load_actuals(conn, symbols)
    where, params = ("where symbol = any(%s)", [symbols]) if symbols else ("", [])

    with conn.cursor() as cur:
        cur.execute(f"""select id, symbol, metric, target_value, target_period,
                               basis, period_type, quote, said_on
                        from promises {where}""", params)
        rows = cur.fetchall()

    empty = Actuals({}, {})
    tally = {STATUS_KEPT: 0, STATUS_MISSED: 0, STATUS_OPEN: 0}
    updates = []
    now = datetime.now(timezone.utc)

    for (ident, symbol, metric, target_value, target_period,
         basis, period_type, quote, said_on) in rows:
        # Every assumption the scorer made is recorded beside the verdict it led to.
        assumed: list[str] = []
        blocked: str | None = None
        status, actual, how = STATUS_OPEN, None, None

        label, why_period = effective_period(target_period, quote, said_on)
        if why_period:
            period_type = period_type or periods.YEAR
            assumed.append(why_period)

        if basis == "unclear" and not _SCOPE_WORDS.search(quote or ""):
            basis = BASIS_REPORTED
            assumed.append(ASSUMED_REPORTED)

        span, reading = read_target(target_value or "", metric)
        if reading:
            assumed.append(reading)
        parsed = periods.parse_period(label or "")

        if span is None:
            blocked = f"target {target_value!r} has no number to compare"
        elif parsed is None:
            blocked = (f"target period {label!r} could not be read"
                       if label else "no target period was stated")
        elif said_on is not None and parsed[0] < said_on:
            blocked = MISLABELLED_NOTE
        elif (unsound := comparable(basis, period_type, label, parsed[1])):
            blocked = unsound
        else:
            value, why = actual_for(metric, actuals.get(symbol, empty),
                                    parsed[0], period_type)
            if value is None:
                # No actual, and so no mapping either: the metric may not be one
                # the reported statements carry at all.
                blocked = why
            else:
                if _ACTUAL_COLUMN[metric][2] == MONEY:
                    span = in_crore(span, target_value or "")
                blocked = company_scale(metric, span, value)
                if blocked is None and not _METRIC_WORDS.search(quote or ""):
                    blocked = NO_METRIC_NOTE
                if blocked is None:
                    how = direction(target_value or "")
                    status = verdict(span, value, how)
                    actual = value

        note = "; ".join(assumed + ([blocked] if blocked else [])) or None
        tally[status] += 1
        updates.append((status,
                        None if actual is None else f"{actual:.2f}",
                        now if status != STATUS_OPEN else None,
                        how, note, label, ident))

    if updates:
        with conn.cursor() as cur:
            cur.executemany("""update promises set status = %s, actual_value = %s,
                               checked_at = %s, comparison = %s, scoring_note = %s,
                               target_period = %s
                               where id = %s""", updates)
        conn.commit()
    return tally


# ------------------------------------------------------------------------- reporting

def show(conn: psycopg.Connection, symbol: str) -> None:
    with conn.cursor() as cur:
        cur.execute("""select p.metric, p.target_value, p.target_period, p.status,
                              p.actual_value, p.comparison, p.scoring_note, p.quote,
                              p.said_on, d.period, p.model_used,
                              p.basis, p.period_type
                       from promises p left join documents d on d.id = p.source_doc
                       where p.symbol = %s
                       order by case p.status when 'missed' then 0 when 'kept' then 1
                                              else 2 end, p.target_period""",
                    (symbol,))
        rows = cur.fetchall()

    print(f"\n{'=' * 78}\n{symbol}: {len(rows)} promises in the ledger\n{'=' * 78}")
    for (metric, target, period, status, actual, how, note, quote, said_on,
         doc_period, model, basis, period_type) in rows:
        verdict_line = f"[{status.upper()}]"
        if actual is not None:
            verdict_line += f" actual {actual} vs {how} {target}"
        print(f"\n  {metric:<15} {str(target):<22} {str(period):<8} {verdict_line}")
        print(f"  basis {basis or '-'}, {period_type or '-'} target"
              f"   said on {said_on} on the {doc_period} call"
              f"   model {model or '-'}")
        if note:
            print(f"  still open: {note}")
        print(f'  "{quote}"')

    with conn.cursor() as cur:
        cur.execute("""select status, count(*) from promises where symbol = %s
                       group by 1 order by 2 desc""", (symbol,))
        by_status = cur.fetchall()
        cur.execute("""select count(*) from promises
                       where symbol = %s and checked_at is not null""", (symbol,))
        scored = cur.fetchone()[0]
        cur.execute("""select speaker_mode, count(*), sum(mgmt_chars), sum(char_count)
                       from documents where symbol = %s group by 1""", (symbol,))
        modes = cur.fetchall()
        cur.execute("""select coalesce(sum(pass1_rejected), 0),
                              coalesce(sum(pass2_rejected), 0)
                       from documents where symbol = %s""", (symbol,))
        pass1, pass2 = cur.fetchone()
        cur.execute("""select coalesce(basis, 'not recorded'),
                              coalesce(period_type, 'not recorded'), count(*)
                       from promises where symbol = %s group by 1, 2
                       order by 3 desc""", (symbol,))
        bases = cur.fetchall()
        cur.execute("""select coalesce(model_used, 'none'), count(*) from promises
                       where symbol = %s group by 1 order by 2 desc""", (symbol,))
        models = cur.fetchall()

    print(f"\n{'-' * 78}\n  by status:   " +
          ", ".join(f"{s}={n}" for s, n in by_status))
    print(f"  scored:      {scored} of {len(rows)} "
          f"({len(rows) - scored} still open, each with a reason above)")
    for mode, n, mgmt, total in modes:
        print(f"  transcripts: {n} {mode}, {mgmt:,} of {total:,} chars "
              f"attributed to management")
    print(f"  rejected:    {pass1} sentences failed the chunk verbatim check, "
          f"{pass2} quotes failed the document verbatim check")
    print("  basis:       " +
          ", ".join(f"{b}/{pt}={n}" for b, pt, n in bases))
    print("  pass 2 by:   " + ", ".join(f"{m}={n}" for m, n in models))


def prune_questions(conn: psycopg.Connection, symbols: list[str]) -> int:
    """Delete promises whose quote is a question: an analyst asking, not management."""
    with conn.cursor() as cur:
        cur.execute("""delete from promises
                       where symbol = any(%s)
                         and rtrim(quote, ' "''”’') like '%%?'""", (symbols,))
        removed = cur.rowcount
    conn.commit()
    return removed


def dedupe(conn: psycopg.Connection, symbols: list[str]) -> int:
    """Delete repeats of one commitment, keeping the earliest call's quote.

    The same symbol, metric, period and target is one promise however many calls
    restate it; counted twice it would weigh twice in the track record. A promise
    with no period is never a duplicate, because nothing says two of them are the
    same commitment.
    """
    with conn.cursor() as cur:
        cur.execute("""delete from promises where id in (
                         select id from (
                           select id, row_number() over (
                             partition by symbol, metric, target_period,
                                          lower(regexp_replace(coalesce(target_value, ''),
                                                               '\\s+', '', 'g'))
                             order by said_on nulls last, id) as rk
                           from promises
                           where symbol = any(%s) and target_period is not null) ranked
                         where rk > 1)""", (symbols,))
        removed = cur.rowcount
    conn.commit()
    return removed


def _reset(conn: psycopg.Connection, symbols: list[str]) -> int:
    """Forget these symbols' promises so both passes run again.

    Needed when the pass 2 contract changes: a promise written before the basis guard
    carries no basis and could never earn a verdict again. The cheap pass 1 calls are
    in the disk cache, so this costs one strong call per transcript.
    """
    with conn.cursor() as cur:
        cur.execute("delete from promises where symbol = any(%s)", (symbols,))
        dropped = cur.rowcount
        cur.execute("""update documents set promises_built_at = null
                       where symbol = any(%s) and doc_type = 'concall'""", (symbols,))
    conn.commit()
    return dropped


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and score management promises")
    parser.add_argument("--only", action="append", help="limit to these symbols")
    parser.add_argument("--show", metavar="SYMBOL", help="print the ledger and stop")
    parser.add_argument("--score-only", action="store_true", help="re-score, no extraction")
    parser.add_argument("--reextract", action="store_true",
                        help="drop these symbols' promises and run both passes again")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with connect() as conn:
        if args.show:
            show(conn, args.show.upper())
            return

        symbols = args.only or [c.symbol for c in COMPANIES]
        rejected = Rejections()

        if args.reextract and not args.score_only:
            log.info("cleared %d promises for re-extraction", _reset(conn, symbols))

        if not args.score_only:
            with conn.cursor() as cur:
                cur.execute("""select d.id, d.symbol, d.period, d.text_path, d.doc_date
                               from documents d
                               where d.doc_type = 'concall' and d.symbol = any(%s)
                                 and d.promises_built_at is null
                               order by d.symbol, d.doc_date desc""", (symbols,))
                cols = [c[0] for c in cur.description]
                docs = [dict(zip(cols, r)) for r in cur.fetchall()]

            log.info("%d transcripts still need promise extraction", len(docs))
            written = sum(extract_document(conn, d, rejected) for d in docs)
            log.info("wrote %d promises", written)
            log.info("verbatim rejections: %d at the chunk check, %d at the document "
                     "check; %d questions dropped; %d commitments carried no deadline; "
                     "%d model calls never answered", rejected.pass1, rejected.pass2,
                     rejected.question, rejected.no_period, rejected.failed)

        log.info("removed %d promises whose quote is a question", prune_questions(conn, symbols))
        # Scored once to settle every label, so repeats are found on the period each
        # promise is actually judged against, then again over what survives.
        score(conn, symbols)
        log.info("removed %d duplicate promises", dedupe(conn, symbols))
        tally = score(conn, symbols)
        log.info("scored: kept=%d missed=%d open=%d",
                 tally[STATUS_KEPT], tally[STATUS_MISSED], tally[STATUS_OPEN])


if __name__ == "__main__":
    main()

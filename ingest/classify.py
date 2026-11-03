# Category classification in stages, cheapest first: title rules, then BSE's own
# subcategory, then an ambiguity test that holds boilerplate titles at routine
# weight, then a cheap model for whatever is left. Most filings never reach the
# model, because BSE titles are formulaic.

from __future__ import annotations

import logging
import re
from typing import Final

from ingest.llm import LLMError, generate_json

log = logging.getLogger(__name__)

CATEGORIES: Final[tuple[str, ...]] = (
    "results", "M&A", "fund_raise", "management_change", "litigation",
    "insider_trading", "earnings_call", "investor_meetings", "credit_rating",
    "dividend", "bonus_split", "capex", "press_release", "postal_ballot",
    "agm_egm", "pledging", "unclassified",
)

# Routine first. Compliance filings borrow the vocabulary of material ones:
# "Appointment of Statutory Auditors" matches `appointment` and would score 55 as a
# management change; "Closure of Trading Window" matches `pit regulation` and would
# score 25 as insider trading. Repaying a commercial paper and republishing a fund
# raise notice in a newspaper are the same trap: both are routine, weight 3.
# Short tokens carry word boundaries or `agm` matches inside unrelated words.
#
# Notices come before the event they announce. A filing that says results land on a
# future date is a calendar entry, not the numbers, so it sits at 25 and the results
# filing a fortnight later carries the 55.
RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (label, re.compile(pattern, re.I)) for label, pattern in (
        ("unclassified",      r"\bauditor|\besop\b|allotment of equity shares under|"
                              r"grant of (stock )?options|employee stock option|"
                              r"compliance certificate|certificate under regulation|"
                              r"certificate under reg|newspaper (publication|clipping)|"
                              r"trading window|esg report|"
                              r"\brepayment\b|\bredemption\b|payment of interest|"
                              r"interest payment|redemption of"),
        ("earnings_call",     r"earnings call|conference call|concall|transcript|"
                              r"audio recording|to announce.{0,60}results|"
                              r"intimation of (the )?date of (the )?board meeting"),
        ("results",           r"financial results|audited results|board meeting.*results|"
                              r"results for the quarter|results presentation|"
                              r"earnings presentation"),
        ("dividend",          r"dividend|record date.*dividend"),
        ("management_change", r"resignation|appointment|cessation|\bceo\b|\bcfo\b|"
                              r"managing director|key managerial"),
        ("M&A",               r"acquisition|acquire|merger|amalgamation|demerger|"
                              r"joint venture|scheme of arrangement"),
        ("fund_raise",        r"rights issue|\bqip\b|qualified institutional|"
                              r"preferential allotment|buyback|commercial paper|\bncd\b"),
        ("credit_rating",     r"credit rating|rating agency|icra|crisil|care ratings|india ratings"),
        ("litigation",        r"order.*sebi|penalty|show cause|litigation|nclt|insolvency|tribunal"),
        ("insider_trading",   r"\bsast\b|pit regulation|disclosure under regulation 7|insider"),
        ("pledging",          r"pledge|encumbrance|invocation of pledge"),
        ("bonus_split",       r"bonus issue|stock split|sub-division|face value"),
        ("capex",             r"capacity expansion|new plant|greenfield|brownfield|"
                              r"capital expenditure"),
        ("agm_egm",           r"annual general meeting|extraordinary general meeting|"
                              r"\bagm\b|\begm\b"),
        ("postal_ballot",     r"postal ballot"),
        ("press_release",     r"bagging|receiving of order|receipt of order|award of order|"
                              r"analyst presentation|investor presentation|press release|"
                              r"media release"),
        ("investor_meetings", r"investor.{0,4}meet|analyst.{0,4}meet|schedule of.*investor|"
                              r"institutional investor"),
    )
)

HIGH_WEIGHT: Final = frozenset({"results", "M&A", "management_change", "fund_raise", "litigation"})

# BSE's own labels. SUBCATNAME carries the signal; CATEGORYNAME is mostly
# "Company Update", which says nothing on its own.
#
# "Allotment of Equity Shares" and "Issue of Securities" are deliberately absent.
# Both cover an ESOP grant, a debenture tranche and a QIP alike, so they fall
# through to the ambiguity test below and are resolved from the filing text.
BSE_SUBCATEGORY: Final[dict[str, str]] = {
    "financial results": "results",
    "earnings call transcript": "earnings_call",
    "analyst / investor meet": "investor_meetings",
    "investor presentation": "press_release",
    "press release / media release": "press_release",
    "award of order / receipt of order": "press_release",
    "acquisition": "M&A",
    "newspaper publication": "unclassified",
    "allotment of esop / esps": "unclassified",
    "change in management": "management_change",
    "clarification": "press_release",
    "scheme of arrangement": "M&A",
    "change in directors": "management_change",
    "change in auditor": "unclassified",
    "credit rating": "credit_rating",
}

# Matched on the start of the subcategory, for labels BSE suffixes with circular
# references, e.g. "Half Yearly Report (SEBI Circular ...)".
BSE_SUBCATEGORY_PREFIX: Final[tuple[tuple[str, str], ...]] = (
    ("half yearly report", "unclassified"),
    ("memorandum of understanding", "M&A"),
    ("certificate under", "unclassified"),
    ("statement of deviation", "unclassified"),
)
BSE_CATEGORY: Final[dict[str, str]] = {
    "result": "results",
    "agm/egm": "agm_egm",
    "insider trading / sast": "insider_trading",
}

# What classified_by records for a title held back for the filing text to resolve.
AMBIGUOUS_BY: Final = "ambiguous_title"

# The regulation citation every Reg 30 title carries, which never says what the
# filing is about.
_CITATION: Final = re.compile(
    r"securities (and|&) exchange board of india|sebi|lodr|"
    r"listing obligations?\s*(and|&)?\s*disclosure requirements?|"
    r"regulations?,?\s*\d{4}|regulations?\s*\d+\s*(\(\d+\))?", re.I)

# Meeting furniture, company suffixes and dates. What survives both patterns is
# the payload: the part of the title that says what actually happened.
_SCAFFOLD: Final = re.compile(
    r"\b(announcements?|disclosures?|intimations?|submissions?|updates?|outcome|"
    r"board|meetings?|directors?|held|inter|alia|considerations?|considering|consider|"
    r"approvals?|approving|approve|issuance|on|of|for|the|a|an|and|or|other|applicable|"
    r"under|pursuant|to|regulations?|reg|company|companies|limited|ltd|general|others|"
    r"dated?|day|january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\b", re.I)

# A payload that names a bucket too broad to weigh on its own.
_AMBIGUOUS_RESIDUE: Final = re.compile(r"^(allotment|issue securities)$")

# Words a company's own filing title carries without saying anything.
_NAME_NOISE: Final = frozenset({"limited", "ltd", "india", "indian", "industries",
                                "bank", "technologies", "enterprises"})


def payload(title: str, name: str = "") -> str:
    """The title with citations, furniture, dates and the issuer's name removed.

    Tokens under three letters go too: an ordinal survives date stripping as a
    bare "th", which would read as payload and it is not.
    """
    stripped = _SCAFFOLD.sub(" ", _CITATION.sub(" ", title))
    words = re.sub(r"[^a-z\s]", " ", stripped.lower()).split()
    issuer = set(re.sub(r"[^a-z\s]", " ", name.lower()).split()) | _NAME_NOISE
    return " ".join(w for w in words if len(w) > 2 and w not in issuer)


def is_ambiguous(title: str, name: str = "") -> bool:
    """True when nothing informative survives in the title.

    "Disclosure Under Regulation 30 Of SEBI (LODR) Regulations, 2015" and
    "Outcome Of Board Meeting Held On 19 October 2026" name a regulation and a
    date and nothing else; "...-Allotment" names a bucket spanning an ESOP grant
    and a QIP. A title only guess on these lands in a weight 55 category about as
    often as not, so they are held at routine weight until the filing text decides.
    """
    residue = payload(title, name)
    return not residue or bool(_AMBIGUOUS_RESIDUE.match(residue))


def by_rules(title: str) -> str | None:
    """First matching rule, routine patterns first. None when nothing matches."""
    hit: str | None = None
    for label, pattern in RULES:
        if pattern.search(title):
            hit = label
            break

    if hit == "unclassified":
        collided = [lbl for lbl, pat in RULES
                    if lbl in HIGH_WEIGHT and pat.search(title)]
        if collided:
            log.warning("routine pattern won over %s for title: %s",
                        "/".join(collided), title[:90])
    return hit


def by_bse(category: str | None, subcategory: str | None) -> str | None:
    sub = " ".join((subcategory or "").lower().split())
    if sub in BSE_SUBCATEGORY:
        return BSE_SUBCATEGORY[sub]
    for prefix, label in BSE_SUBCATEGORY_PREFIX:
        if sub.startswith(prefix):
            return label
    return BSE_CATEGORY.get(" ".join((category or "").lower().split()))


_SCHEMA: Final = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "category": {"type": "string", "enum": list(CATEGORIES)},
        },
        "required": ["i", "category"],
    },
}
_SYSTEM: Final = (
    "You label Indian stock exchange filing titles. Reply with one category per "
    "title, chosen only from the allowed list. Use 'unclassified' for routine "
    "compliance filings that carry no new information about the business."
)
BATCH_SIZE: Final = 20


_PDF_SCHEMA: Final = {
    "type": "object",
    "properties": {"category": {"type": "string", "enum": list(CATEGORIES)}},
    "required": ["category"],
}
_PDF_SYSTEM: Final = (
    "You label Indian stock exchange filings by reading the filing itself. Use only "
    "the text provided. Do not use any outside knowledge about the company, and do "
    "not infer anything the text does not say. Choose exactly one category from the "
    "allowed list. Use 'unclassified' for a routine compliance filing that carries no "
    "new information about the business."
)
PDF_BY: Final = "pdf_text"


def by_pdf_text(body: str) -> tuple[str, str] | None:
    """(label, model) read off the filing text. None when the model refused.

    This is the pass that resolves what a title could not. A boilerplate title and a
    title-only model guess are both re-read here regardless of the score they already
    carry, because a wrong low label hides a filing rather than merely misfiling it.
    """
    if not body.strip():
        return None
    try:
        parsed, meta = generate_json(
            "Classify this filing.\n\n--- filing text ---\n" + body,
            schema=_PDF_SCHEMA, tier="cheap", system=_PDF_SYSTEM, max_tokens=256)
    except LLMError as e:
        log.error("pdf classifier call failed: %s", e)
        return None

    label = parsed.get("category")
    if label not in CATEGORIES:
        log.warning("pdf classifier returned %r, not a category", label)
        return None
    return label, f"{PDF_BY}:{meta.model_used}"


def by_model(titles: list[str]) -> dict[int, tuple[str, str]]:
    """(label, model) per title index for what nothing deterministic resolved."""
    labels: dict[int, tuple[str, str]] = {}
    for start in range(0, len(titles), BATCH_SIZE):
        chunk = titles[start:start + BATCH_SIZE]
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(chunk))
        try:
            parsed, meta = generate_json(
                f"Label each title.\n\n{listing}", schema=_SCHEMA,
                tier="cheap", system=_SYSTEM, max_tokens=2048)
        except LLMError as e:
            log.error("classifier model call failed for %d titles: %s", len(chunk), e)
            continue

        for item in parsed:
            index = start + int(item["i"])
            if 0 <= index - start < len(chunk) and item["category"] in CATEGORIES:
                labels[index] = (item["category"], meta.model_used)
    return labels

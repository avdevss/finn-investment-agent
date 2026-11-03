# Splits a concall transcript into turns and says who was speaking.
#
# A promise is something management said. An analyst asking "so do you expect 15%
# margins?" must never become a management commitment, so the question side is
# removed before anything is extracted.
#
# Layouts differ by company, so who is speaking is decided from four signals the
# transcripts themselves carry, not from one house style:
#
#   1. the participants block, where there is one ("CORPORATE PARTICIPANTS:",
#      "MANAGEMENT:", "TITAN MANAGEMENT SPEAKERS:", "Call Participants:")
#   2. the moderator's own question announcements, "the next question is from the
#      line of Puneet Gulati from HSBC", which name every analyst who speaks and
#      whose first occurrence is where the Q&A section begins
#   3. whoever holds the floor before that boundary, which is management presenting
#   4. prose introductions, "Mr. Anindya Banerjee - Group Chief Financial Officer",
#      for the four transcripts that carry no participants block at all
#
# Signal 2 is the load bearing one, because it is the only one present in every
# transcript measured. Relying on the participants block alone left TCS, MARUTI,
# ICICIBANK and VBL with no recognised turn but the moderator, and so with every
# management answer swallowed into a turn labelled analyst.
#
# When no labels are found at all the transcript is returned whole and recorded as
# `unsegmented`, rather than quietly implying the filter ran.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

log = logging.getLogger(__name__)

LABELLED: Final = "labelled"
UNSEGMENTED: Final = "unsegmented"

MANAGEMENT: Final = "management"
ANALYST: Final = "analyst"

# The heading that opens the question section. Matched on a short line only, so a
# mention of "Q&A" inside somebody's answer does not move the boundary.
_QA_HEADING: Final = re.compile(r"^(?:q\s*&\s*a|q\s*and\s*a|question[\s-]*and[\s-]*answer)\b", re.I)
_QA_MAX_LEN: Final = 70

# Most transcripts never head the section at all; the moderator just says it. Used
# only when no heading was found, and the first mention wins, because the moderator
# announces the session once and then refers back to it.
_QA_PROSE: Final = re.compile(
    r"\bbegin (?:with )?(?:the |an? )?question[\s-]*and[\s-]*answer|"
    r"\bopen (?:up )?(?:the (?:call|floor|line)s? )?for (?:a )?question|"
    r"\bfor (?:a |an )?question[\s-]*and[\s-]*answer session|"
    r"\bfollowed by (?:a|an) (?:interactive )?q\s*&\s*a|"
    r"\bbegin (?:with )?the q\s*&\s*a", re.I)

# "The next question is from the line of Puneet Gulati from HSBC." Present in every
# transcript measured, which is why it carries the attribution rather than the
# participants block. Each match names an analyst, and the first one is the boundary.
_QUESTION_FROM: Final = re.compile(
    r"(?i:question\s+(?:comes\s+|is\s+|will\s+be\s+)?from\s+"
    r"(?:the\s+line\s+of\s+)?)(?i:(?:mr|mrs|ms|dr|shri)\.?\s*)?"
    r"([A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*){0,3})")

# "Mr. Anindya Banerjee - Group Chief Financial Officer, ICICI Bank", the only place
# some transcripts name their management at all.
_INTRODUCED: Final = re.compile(
    r"(?i:\b(?:mr|mrs|ms|dr|shri|smt)\.\s*)"
    r"([A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*){0,3})")
_INTRODUCED_WINDOW: Final = 70

# "Questioner (Probal Sen - ICICI Securities)", "Company Speaker (Sh ...)".
_BRACKETED_TURN: Final = re.compile(
    r"^(?P<role>questioner|company speaker|moderator|operator|analyst|management|"
    r"participant|speaker)\s*(?:\((?P<who>[^)]*)\))?\s*:?\s*$", re.I)

# "Sh V Srikanth 00:00:01 - 00:09:00 (Group Performance)" in the presentation half.
_TIMESTAMPED_TURN: Final = re.compile(
    r"^(?P<who>(?:sh|smt|mr|ms|dr)?\.?\s*[A-Z][\w.'\-]*(?:\s+[A-Z][\w.'\-]*){0,3})\s+"
    r"\d{1,2}:\d{2}(?::\d{2})?\s*[-–—]", re.I)

# "Moderator:" or "V Srikanth:" - the most common Indian transcript style.
_COLON_TURN: Final = re.compile(
    r"^(?P<who>(?:sh|smt|mr|ms|dr)?\.?\s*[A-Z][\w.'\-]*(?:\s+[A-Z][\w.'\-]*){0,4})\s*:\s*(?P<rest>.*)$")

_ANALYST_ROLES: Final = frozenset({"questioner", "moderator", "operator", "analyst",
                                   "participant"})
_MANAGEMENT_ROLES: Final = frozenset({"company speaker", "management", "speaker"})

# The participants block opens with a heading, which may carry the first entry on
# the same line: "MANAGEMENT: MR. NIYANT MARU- CHIEF FINANCIAL OFFICER -". An issuer
# name may precede the keyword, as in "TITAN MANAGEMENT SPEAKERS:".
_ROSTER_HEADING: Final = re.compile(
    r"^(?:[A-Z][\w.&\-]*\s+){0,2}"
    r"(?:call participants|participants|management speakers|management team|"
    r"management|speakers|corporate participants|company participants)"
    r"\s*:?\s*(?P<rest>.*)$", re.I)
# "Mr. Niyant", "MR.SHASHANK". The dot alone is enough of a boundary, because a PDF
# that lost its spaces keeps its full stops.
_HONORIFIC: Final = re.compile(r"^(sh|shri|smt|mr|mrs|ms|dr|prof)(?:\.\s*|\s+)", re.I)
# The same prefix with the dot gone too, which only extraction damage produces. Kept
# as an extra spelling of the name rather than a replacement, so that a real name
# beginning with these letters is not quietly truncated. "Sh" is deliberately absent:
# Shashank and Shailesh are names, and MRNIYANT is the damage actually seen.
_GLUED_HONORIFIC: Final = re.compile(r"^(?:mr|mrs|ms|dr|prof)(?=[a-z])")
# An analyst label usually carries the firm they are from.
_FIRM_SUFFIX: Final = re.compile(r"[-–—,]\s*\S")

# A roster entry is a name, then its title, then the company, on one line or three.
# Title lines are stepped over; the block ends at a line that is neither.
_ROLE_WORDS: Final = re.compile(
    r"\b(chief|officer|president|vice|executive|managing|deputy|joint|global|"
    r"corporate|segment|industry|director|chairman|chairperson|head|manager|"
    r"secretary|relations|investor|ceo|cfo|coo|cto|cio|md|treasurer|controller|"
    r"founder|partner|analyst|strategy|finance|communications)\b", re.I)
# Words no person's name is made of, which a wrapped title line can otherwise leave
# behind looking like one.
_NOT_A_NAME: Final = frozenset({"and", "the", "of", "for", "from", "with"})
# The issuer's own name sits in these blocks as often as a person's does, and a page
# footer repeating it must never read as a speaker opening a turn.
_CORPORATE: Final = frozenset({"limited", "ltd", "inc", "plc", "llp", "india",
                               "private", "pvt", "corporation", "company", "group",
                               "industries", "holdings"})
# Name and title are divided by a comma, a dash, a pipe or a spaced colon:
# "Mr. Amit Syngle : MD & CEO".
_NAME_SPLIT: Final = re.compile(r"\s*[,–—|:]\s*|\s+-\s+")

# Letterhead keys that are two capitalised words and so would otherwise read as a
# person opening a turn. Every transcript arrives behind a covering letter.
_LETTERHEAD: Final = frozenset({
    "scrip", "code", "membership", "registered", "office", "corporate", "encl",
    "enclosure", "regd", "cin", "isin", "ref", "subject", "sub", "date", "place",
    "venue", "time", "duration", "address", "tel", "fax", "email", "web", "website",
    "notice", "annexure", "attention", "dear", "yours", "thanking", "disclaimer",
    "transcript", "presentation", "link", "audio", "sensitivity", "page", "note"})


@dataclass(frozen=True)
class Turn:
    side: str
    who: str
    text: str


@dataclass(frozen=True)
class Attributed:
    mode: str
    turns: list[Turn]

    @property
    def management_text(self) -> str:
        if self.mode == UNSEGMENTED:
            return "\n".join(t.text for t in self.turns)
        return "\n".join(t.text for t in self.turns if t.side == MANAGEMENT)

    @property
    def mgmt_chars(self) -> int:
        return len(self.management_text)


def _normalise_name(raw: str) -> str:
    return " ".join(_HONORIFIC.sub("", raw.strip()).lower().split())


def _roster_name(body: str) -> set[str]:
    """Every spelling of the person named on one roster line. Empty if it names none.

    The name runs up to the title, so the title's first word is the boundary rather
    than any punctuation: one real transcript renders the entry as
    "MRNIYANT MARU CHIEF FINANCIAL OFFICER", spaces and dashes both lost, and only
    the word "CHIEF" still marks where the name ended.
    """
    head = _NAME_SPLIT.split(body)[0]
    role = _ROLE_WORDS.search(head)
    if role is not None:
        head = head[:role.start()]

    name = _normalise_name(head)
    words = name.split()
    if not 2 <= len(words) <= 4 or not re.fullmatch(r"[a-z.'\- ]+", name):
        return set()
    if _CORPORATE & set(words) or _NOT_A_NAME & set(words):
        return set()

    spellings = {name}
    glued = _GLUED_HONORIFIC.sub("", name)
    if glued != name and len(glued.split()[0]) >= 3:
        spellings.add(glued)
    return spellings


def roster(text: str) -> set[str]:
    """Management names listed in the transcript's own participants block.

    A block runs name, title, company and then the next person, so title lines are
    stepped over rather than ending it. It ends at anything that is neither, which is
    also what keeps the journalists listed under their own heading out of it.
    """
    names: set[str] = set()
    lines = text.splitlines()

    for i, line in enumerate(lines):
        heading = _ROSTER_HEADING.match(line.strip().lstrip("•*- "))
        if heading is None:
            continue

        block = [heading.group("rest")] + lines[i + 1:i + 25]
        for candidate in block:
            body = candidate.strip().lstrip("•*-– ").strip()
            if not body:
                continue
            if len(body) > 110 or body.endswith((".", "?")):
                break

            found = _roster_name(body)
            if found:
                names |= found
            elif _ROLE_WORDS.search(body) or _CORPORATE & set(body.lower().split()):
                continue    # the person's title, or the company they work for
            else:
                break       # the block is over, and whatever follows is not ours
    return names


def _plausible_name(name: str) -> bool:
    """Two to four alphabetic words that are not letterhead, a title or a company."""
    words = name.split()
    if not 2 <= len(words) <= 4 or not re.fullmatch(r"[a-z.'\- ]+", name):
        return False
    bag = set(words)
    return not (bag & _LETTERHEAD or bag & _CORPORATE or bag & _NOT_A_NAME
                or _ROLE_WORDS.search(name))


def announced_analysts(lines: list[str]) -> tuple[set[str], int]:
    """(names the moderator called on, index of the first such call).

    The moderator announces every questioner by name, so this is both the analyst
    roster and the most reliable Q&A boundary available.
    """
    names: set[str] = set()
    first = len(lines)
    for i, line in enumerate(lines):
        for m in _QUESTION_FROM.finditer(line):
            name = _normalise_name(m.group(1))
            if _plausible_name(name):
                names.add(name)
                first = min(first, i)
    return names, first


def introduced_management(text: str) -> set[str]:
    """Names given an honorific and a title in prose, for transcripts with no block."""
    names: set[str] = set()
    for m in _INTRODUCED.finditer(text):
        if not _ROLE_WORDS.search(text[m.end():m.end() + _INTRODUCED_WINDOW]):
            continue
        name = _normalise_name(m.group(1))
        if _plausible_name(name):
            names.add(name)
    return names


def _split_qa(lines: list[str]) -> int:
    """Index of the first Q&A line. len(lines) when the transcript has no Q&A."""
    found = len(lines)
    for i, line in enumerate(lines):
        body = line.strip()
        if len(body) <= _QA_MAX_LEN and _QA_HEADING.match(body):
            found = i
    if found < len(lines):
        return found

    return next((i for i, line in enumerate(lines) if _QA_PROSE.search(line)),
                len(lines))


@dataclass(frozen=True)
class Opener:
    """A line that starts a turn, before anyone has decided whose turn it is."""
    line: int
    who: str
    role: str | None        # set when the label was a role rather than a name
    rest: str


def _opener_at(index: int, line: str, names: set[str]) -> Opener | None:
    """Structure only: does this line start a turn, and whose name is on it."""
    body = line.strip()
    if not body:
        return None

    m = _BRACKETED_TURN.match(body)
    if m:
        role, who = m.group("role"), m.group("who") or ""
        return Opener(index, who or role, role, "")

    m = _TIMESTAMPED_TURN.match(body)
    if m:
        return Opener(index, m.group("who"), None, "")

    # A roster name alone on a line is how a transcript with no colons marks a turn.
    if _normalise_name(body) in names:
        return Opener(index, body, None, "")

    m = _COLON_TURN.match(body)
    if m:
        who, rest = m.group("who"), m.group("rest")
        label = who.lower().strip()
        if label in _ANALYST_ROLES or label in _MANAGEMENT_ROLES:
            return Opener(index, who, label, rest)
        # Any plausible person's name opens a turn. The guard is what a name may not
        # be: letterhead, a job title or a company, which is what "Scrip Code:" and
        # "Registered Office:" in the covering letter would otherwise look like.
        if _normalise_name(who) in names or _plausible_name(_normalise_name(who)) \
                or _FIRM_SUFFIX.search(who):
            return Opener(index, who, None, rest)
    return None


def _sides(openers: list[Opener], text: str, block: set[str], analysts: set[str],
           qa_start: int) -> dict[int, str]:
    """Which side each opener belongs to, decided once for the whole document."""
    presenting = {_normalise_name(o.who) for o in openers
                  if o.role is None and o.line < qa_start}
    management = block | presenting | introduced_management(text)
    # The participants block is the issuer's own statement of who speaks for it, so
    # it outranks a name the moderator happened to call on.
    analysts = analysts - block

    sides: dict[int, str] = {}
    for o in openers:
        name = _normalise_name(o.who)
        if o.role and o.role in _ANALYST_ROLES:
            sides[o.line] = ANALYST
        elif o.role and o.role in _MANAGEMENT_ROLES:
            sides[o.line] = MANAGEMENT
        elif name in analysts:
            sides[o.line] = ANALYST
        elif name in management or o.line < qa_start:
            sides[o.line] = MANAGEMENT
        else:
            # An unrecognised voice in the question half stays out. Losing a
            # commitment costs less than crediting management with an analyst's words.
            sides[o.line] = ANALYST
    return sides


def attribute(text: str) -> Attributed:
    """Turns with a side each, or the whole transcript marked unsegmented."""
    lines = text.splitlines()
    block = roster(text)
    analysts, first_question = announced_analysts(lines)
    qa_start = min(_split_qa(lines), first_question)

    openers = [o for o in (_opener_at(i, line, block)
                           for i, line in enumerate(lines)) if o is not None]
    if len(openers) < 2:
        log.info("no speaker labels recognised, keeping the transcript unsegmented")
        return Attributed(UNSEGMENTED, [Turn(MANAGEMENT, "unsegmented", text)])

    sides = _sides(openers, text, block, analysts, qa_start)
    opener_at = {o.line: o for o in openers}

    turns: list[Turn] = []
    side, who, buffer = MANAGEMENT, "presentation", []

    for i, line in enumerate(lines):
        opened = opener_at.get(i)
        if opened is None:
            buffer.append(line)
            continue

        if buffer:
            turns.append(Turn(side, who, "\n".join(buffer).strip()))
        side, who = sides[i], opened.who
        buffer = [opened.rest] if opened.rest else []

    if buffer:
        turns.append(Turn(side, who, "\n".join(buffer).strip()))

    return Attributed(LABELLED, [t for t in turns if t.text])

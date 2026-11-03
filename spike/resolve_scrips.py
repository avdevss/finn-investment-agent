# Throwaway: resolves each NSE symbol to its BSE scrip code once, then prints a
# literal list to paste into ingest/seed_companies.py. Nothing in ingest/ looks a
# scrip code up at runtime.
#
# Matching is on exact BSE symbol equality, never on the first name hit. A name
# search for "Infosys" returns HCL Infosystems (scrip 500179) ahead of Infosys
# (500209), so a first-hit resolver silently seeds the wrong company.

import re
import sys
from typing import Final

from ingest.http import BSE_API_HEADERS, get

LOOKUP: Final = "https://api.bseindia.com/Msource/1D/getQouteSearch.aspx"
PATTERN: Final = re.compile(r"/stock-share-price/([^/]+)/([^/]+)/(\d{6})/")

COMPANIES: Final[list[tuple[str, str, str]]] = [
    ("RELIANCE",   "Reliance Industries",         "Energy"),
    ("TCS",        "Tata Consultancy Services",   "IT"),
    ("INFY",       "Infosys",                     "IT"),
    ("HDFCBANK",   "HDFC Bank",                   "Banking"),
    ("ICICIBANK",  "ICICI Bank",                  "Banking"),
    ("ITC",        "ITC",                         "FMCG"),
    ("LT",         "Larsen",                      "Infrastructure"),
    ("SUNPHARMA",  "Sun Pharmaceutical",          "Pharma"),
    ("MARUTI",     "Maruti Suzuki",               "Auto"),
    ("BAJFINANCE", "Bajaj Finance",               "NBFC"),
    ("ASIANPAINT", "Asian Paints",                "Paints"),
    ("TITAN",      "Titan Company",               "Consumer"),
    ("DMART",      "Avenue Supermarts",           "Retail"),
    ("ADANIPORTS", "Adani Ports",                 "Ports"),
    ("POLYCAB",    "Polycab India",               "Cables"),
    ("DIXON",      "Dixon Technologies",          "Electronics"),
    ("IRCTC",      "Indian Railway Catering",     "Travel"),
    ("DEEPAKNTR",  "Deepak Nitrite",              "Chemicals"),
    ("KPITTECH",   "KPIT Technologies",           "IT"),
    ("VBL",        "Varun Beverages",             "Beverages"),
    ("CDSL",       "Central Depository Services", "Financial Infra"),
    ("ASTRAL",     "Astral",                      "Pipes"),
]
PORTFOLIO_SIZE: Final = 18

# Listed on NSE only, so BSE has no scrip code and no announcements feed. Declared
# here so a missing code is a stated exception rather than a silent null. Verified:
# CDSL.BO is a 404 on Yahoo and BSE's own lookup has no CDSL entry.
NSE_ONLY: Final = {"CDSL"}


def candidates(text: str) -> list[tuple[str, str]]:
    """(bse_symbol, scrip) pairs for a search string, deduplicated."""
    r = get(LOOKUP, headers=BSE_API_HEADERS,
            params={"Type": "EQ", "text": text, "flag": "site"}, attempts=3, timeout=25)
    return sorted({(sym.upper(), scrip) for _, sym, scrip in PATTERN.findall(r.text)})


def resolve(symbol: str, name: str) -> tuple[str, list[str]]:
    """Scrip code for an exact symbol match. Returns ('', seen) when there is none."""
    seen: list[str] = []
    for query in (symbol, name):
        found = candidates(query)
        seen += [f"{s}/{c}" for s, c in found]
        for bse_symbol, scrip in found:
            if bse_symbol == symbol:
                return scrip, seen
    return "", seen


def main() -> int:
    rows: list[tuple[str, str, str, str, str, bool]] = []
    failed: list[str] = []

    for i, (symbol, name, sector) in enumerate(COMPANIES):
        in_portfolio = i < PORTFOLIO_SIZE
        try:
            scrip, seen = resolve(symbol, name)
        except Exception as e:
            print(f"  {symbol:<11} ERROR {type(e).__name__}", file=sys.stderr)
            failed.append(f"{symbol} ({type(e).__name__})")
            continue

        if not scrip:
            if symbol in NSE_ONLY:
                print(f"  {symbol:<11} ------  NSE only, no BSE scrip", file=sys.stderr)
                rows.append((symbol, name, sector, "", symbol.lower(), in_portfolio))
            else:
                print(f"  {symbol:<11} NO EXACT MATCH, saw {seen[:6]}", file=sys.stderr)
                failed.append(f"{symbol} (no exact symbol match)")
            continue

        rows.append((symbol, name, sector, scrip, symbol.lower(), in_portfolio))
        print(f"  {symbol:<11} {scrip}  {name}", file=sys.stderr)

    print(f"\nresolved {len(rows)}/{len(COMPANIES)}", file=sys.stderr)
    if failed:
        raise SystemExit(f"unresolved, fix before seeding: {', '.join(failed)}")

    print("COMPANIES: Final[list[Company]] = [")
    for symbol, name, sector, scrip, slug, in_portfolio in rows:
        code = f'"{scrip}"' if scrip else "None"
        print(f'    Company("{symbol}", "{name}", "{sector}", {code}, "{slug}", {in_portfolio}),')
    print("]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

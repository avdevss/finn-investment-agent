# The company universe: 18 portfolio names plus 4 ad hoc research names.
# BSE scrip codes were resolved once by spike/resolve_scrips.py and are frozen
# here, so no runtime lookup. Names are the display strings used in the UI.

from __future__ import annotations

import logging
from typing import Final, NamedTuple

from ingest.db import connect, upsert

log = logging.getLogger(__name__)


# Lenders have no EBITDA: interest is their cost of goods, not a financing line
# below it, so the usual derivation would be a fiction. Detected by sector, and the
# results extractor reports total income, PBT and PAT for these names instead.
FINANCIAL_SECTORS: Final[frozenset[str]] = frozenset({"Banking", "NBFC"})


class Company(NamedTuple):
    symbol: str
    name: str
    sector: str
    bse_scrip: str | None      # None for NSE only listings
    screener_slug: str
    in_portfolio: bool


COMPANIES: Final[list[Company]] = [
    Company("RELIANCE",   "Reliance Industries",  "Energy",          "500325", "RELIANCE",   True),
    Company("TCS",        "TCS",                  "IT",              "532540", "TCS",        True),
    Company("INFY",       "Infosys",              "IT",              "500209", "INFY",       True),
    Company("HDFCBANK",   "HDFC Bank",            "Banking",         "500180", "HDFCBANK",   True),
    Company("ICICIBANK",  "ICICI Bank",           "Banking",         "532174", "ICICIBANK",  True),
    Company("ITC",        "ITC",                  "FMCG",            "500875", "ITC",        True),
    Company("LT",         "Larsen & Toubro",      "Infrastructure",  "500510", "LT",         True),
    Company("SUNPHARMA",  "Sun Pharma",           "Pharma",          "524715", "SUNPHARMA",  True),
    Company("MARUTI",     "Maruti Suzuki",        "Auto",            "532500", "MARUTI",     True),
    Company("BAJFINANCE", "Bajaj Finance",        "NBFC",            "500034", "BAJFINANCE", True),
    Company("ASIANPAINT", "Asian Paints",         "Paints",          "500820", "ASIANPAINT", True),
    Company("TITAN",      "Titan",                "Consumer",        "500114", "TITAN",      True),
    Company("DMART",      "Avenue Supermarts",    "Retail",          "540376", "DMART",      True),
    Company("ADANIPORTS", "Adani Ports",          "Ports",           "532921", "ADANIPORTS", True),
    Company("POLYCAB",    "Polycab India",        "Cables",          "542652", "POLYCAB",    True),
    Company("DIXON",      "Dixon Technologies",   "Electronics",     "540699", "DIXON",      True),
    Company("IRCTC",      "IRCTC",                "Travel",          "542830", "IRCTC",      True),
    Company("DEEPAKNTR",  "Deepak Nitrite",       "Chemicals",       "506401", "DEEPAKNTR",  True),
    Company("KPITTECH",   "KPIT Technologies",    "IT",              "542651", "KPITTECH",   False),
    Company("VBL",        "Varun Beverages",      "Beverages",       "540180", "VBL",        False),
    Company("CDSL",       "CDSL",                 "Financial Infra", None,     "CDSL",       False),
    Company("ASTRAL",     "Astral",               "Pipes",           "532830", "ASTRAL",     False),
]

PORTFOLIO: Final = [c for c in COMPANIES if c.in_portfolio]
BY_SYMBOL: Final = {c.symbol: c for c in COMPANIES}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with connect() as conn:
        written = upsert(conn, "companies", [c._asdict() for c in COMPANIES], ["symbol"])
    scripless = [c.symbol for c in COMPANIES if not c.bse_scrip]
    log.info("seeded %d companies, %d in portfolio", written, len(PORTFOLIO))
    if scripless:
        log.info("no BSE scrip, announcements will be skipped for: %s", ", ".join(scripless))


if __name__ == "__main__":
    main()

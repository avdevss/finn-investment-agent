# Spike 3: confirm a screener.in company page can be fetched and that a concall
# transcript PDF link can be found in its documents section. Caches the raw HTML.

import pathlib
import re
import sys

import requests
from bs4 import BeautifulSoup

SYMBOL = "RELIANCE"
CACHE = pathlib.Path(__file__).parent / "cache"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}


def fetch(symbol: str) -> str:
    path = CACHE / f"screener_{symbol}.html"
    if path.exists():
        print(f"(cached {path.name})")
        return path.read_text()
    r = requests.get(f"https://www.screener.in/company/{symbol}/consolidated/",
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    CACHE.mkdir(exist_ok=True)
    path.write_text(r.text)
    return r.text


def main() -> int:
    try:
        html = fetch(SYMBOL)
    except requests.RequestException as e:
        print(f"FAIL  screener fetch: {type(e).__name__}: {e}")
        return 1

    soup = BeautifulSoup(html, "lxml")
    docs = soup.find(id="documents")
    if docs is None:
        print("FAIL  no #documents section, markup changed")
        return 1

    links = [(a.get_text(" ", strip=True), a["href"]) for a in docs.find_all("a", href=True)]
    concalls = [(t, h) for t, h in links
                if re.search(r"transcript|concall", f"{t} {h}", re.I)]
    reports = [(t, h) for t, h in links if re.search(r"annual|AnnualReports", h, re.I)]

    print(f"{SYMBOL}: {len(links)} document links, {len(reports)} annual reports, {len(concalls)} concall links")
    for t, h in concalls[:3]:
        print(f"  {t[:30]:<30} {h[:90]}")

    if not concalls:
        print("FAIL  no concall transcript link")
        return 1

    url = concalls[0][1]
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, stream=True, allow_redirects=True)
        r.raise_for_status()
        head = next(r.iter_content(5), b"")
        r.close()
    except requests.RequestException as e:
        print(f"FAIL  transcript fetch: {type(e).__name__}: {e}")
        return 1

    ok = head.startswith(b"%PDF")
    print(f"transcript -> {r.status_code} {r.headers.get('Content-Type')} magic={head!r}")
    print("PASS" if ok else "FAIL  transcript is not a PDF (may be scanned/HTML)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

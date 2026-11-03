# Spike 1: confirm the BSE announcements JSON endpoint answers and that an
# attachment PDF URL built from the response actually resolves.
#
# Two things matter here. Akamai 403s a stale User-Agent, and the AnnGetData/w
# endpoint is retired -- it answers 200 with the string "No Record Found!".
# The live endpoint is AnnSubCategoryGetData/w, which pages.

import sys
from datetime import date, timedelta

import requests

ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
PDF_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
SCRIP = "500325"  # RELIANCE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Origin": "https://www.bseindia.com/",
    "Referer": "https://www.bseindia.com/",
    "Sec-Fetch-Site": "same-site",
}


def main() -> int:
    today = date.today()
    params = {
        "pageno": 1,
        "strCat": "-1",
        "subcategory": "-1",
        "strPrevDate": (today - timedelta(days=30)).strftime("%Y%m%d"),
        "strToDate": today.strftime("%Y%m%d"),
        "strScrip": SCRIP,
        "strSearch": "P",
        "strType": "C",
    }

    try:
        r = requests.get(ANN_URL, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"FAIL  bse announcements: {type(e).__name__}: {e}")
        return 1

    if isinstance(body, str):
        print(f"FAIL  endpoint returned a bare string: {body!r}")
        return 1

    rows = body.get("Table") or []
    total = (body.get("Table1") or [{}])[0].get("ROWCNT")
    print(f"BSE scrip {SCRIP}: {len(rows)} announcements in last 30d (ROWCNT {total})")
    for row in rows[:3]:
        print(f"  {row.get('NEWS_DT')[:19]}  [{row.get('CATEGORYNAME')}]  {str(row.get('NEWSSUB'))[:62]}")

    if not rows:
        print("FAIL  empty Table")
        return 1

    attachment = next((x["ATTACHMENTNAME"] for x in rows if x.get("ATTACHMENTNAME")), None)
    if not attachment:
        print("FAIL  no attachment in any row")
        return 1

    pdf_url = PDF_BASE + attachment
    try:
        p = requests.get(pdf_url, headers=HEADERS, timeout=30, stream=True)
        p.raise_for_status()
        magic = next(p.iter_content(5), b"")
        p.close()
    except requests.RequestException as e:
        print(f"FAIL  pdf fetch: {type(e).__name__}: {e}")
        return 1

    ok = magic.startswith(b"%PDF")
    print(f"pdf  {attachment[:44]} -> {p.status_code} {p.headers.get('Content-Type')} magic={magic!r}")
    print("PASS" if ok else "FAIL  attachment is not a PDF")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

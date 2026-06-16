#!/usr/bin/env python3
"""Search Copart for BMW 330i (or any make/model) matching filters."""
import argparse
import json
import sys
import urllib.request
import urllib.error

URL = "https://www.copart.com/public/lots/search"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.copart.com",
    "Referer": "https://www.copart.com/",
}


def build_query(make, model, page_size, page):
    # Free-text query (more robust than guessing facet field names).
    # We post-filter year/miles/title on the client.
    return {
        "filter": {"MISC": ["sold_flag:false"]},
        "sort": ["odometer_reading_received asc"],
        "size": page_size,
        "from": page * page_size,
        "query": [f"{make} {model}"],
    }


def search(make, model, page_size=100, page=0):
    body = json.dumps(build_query(make, model, page_size, page)).encode()
    req = urllib.request.Request(URL, data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}", file=sys.stderr)
        sys.exit(1)


def matches(lot, make, model, year_min, miles_max, title):
    if (lot.get("mkn") or "").upper() != make.upper():
        return False
    if model.upper() not in (lot.get("lm") or "").upper():
        return False
    if (lot.get("lcy") or 0) < year_min:
        return False
    miles = lot.get("orr") or 0
    if miles > miles_max or miles <= 0:
        return False
    if title == "clean":
        t = (lot.get("td") or "").upper()
        # Accept anything containing CLEAN but reject SALVAGE/REBUILT/SCRAP/etc.
        bad = ("SALVAGE", "REBUILT", "SCRAP", "JUNK", "DISMANTLER", "PARTS",
               "FLOOD", "NON-REPAIRABLE", "CERT OF DESTRUCTION")
        if "CLEAN" not in t or any(b in t for b in bad):
            return False
    return True


def print_lot(lot):
    ln = lot.get("lotNumberStr") or lot.get("ln")
    yr = lot.get("lcy")
    mk = lot.get("mkn")
    md = lot.get("lm")
    miles = int(lot.get("orr") or 0)
    loc = lot.get("yn")
    bid = lot.get("hb") or lot.get("ccb") or 0
    td = lot.get("td")
    url = f"https://www.copart.com/lot/{ln}"
    print(f"  {yr} {mk} {md} | {miles:,} mi | {loc} | title={td} | bid=${bid} | {url}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--make", default="BMW")
    p.add_argument("--model", default="330i")
    p.add_argument("--year-min", type=int, default=2018)
    p.add_argument("--miles-max", type=int, default=75000)
    p.add_argument("--title", default="clean", choices=["clean", "any"])
    p.add_argument("--pages", type=int, default=3, help="pages of 100 to fetch")
    p.add_argument("--raw", action="store_true")
    a = p.parse_args()

    hits, scanned = [], 0
    for pg in range(a.pages):
        data = search(a.make, a.model, page_size=100, page=pg)
        lots = data.get("data", {}).get("results", {}).get("content", [])
        if not lots:
            break
        scanned += len(lots)
        for lot in lots:
            if matches(lot, a.make, a.model, a.year_min, a.miles_max, a.title):
                hits.append(lot)

    if a.raw:
        print(json.dumps(hits, indent=2))
        return
    print(f"Scanned {scanned} lots, {len(hits)} match {a.year_min}+ {a.make} {a.model} "
          f"≤{a.miles_max:,}mi title={a.title}\n")
    for lot in sorted(hits, key=lambda l: l.get("orr") or 0):
        print_lot(lot)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Daily BMW 3/4-series auction search matching buy criteria.

Prints only lots not seen in prior runs. State is stored in seen.json next to
this script.
"""
from datetime import date
from html.parser import HTMLParser
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path(__file__).parent
STATE = HOME / "seen.json"

ZIP = "00000"                          # <your city, ST>
ORIGIN = (0.0000, 0.0000)
MAX_MILES = 250
MAX_ODO = 90_000
YEAR_MIN = 2018
YEAR_MAX = date.today().year + 1

# Damage strings that disqualify a lot. Hail and undercarriage are allowed.
DAMAGE_BLOCK = ("AIRBAG", "FLOOD", "WATER", "FIRE", "BURN", "ROLLOVER",
                "FRAME", "MECHANICAL", "ENGINE", "STRIPPED", "VANDALISM")

MODEL_NUMBERS = ("320", "328", "330", "335", "340",
                 "420", "428", "430", "435", "440")

ABM_SEARCH_URL = "https://www.autobidmaster.com/en/data/v2/inventory/search"
ABM_BASE = "https://www.autobidmaster.com"
ABM_SEARCH_PATH = (
    "make-bmw/doc-type-c/odometer-0,90000/"
    "uorigin-0.0000,0.0000/distance-3"
)

IAAI_SEARCH_URL = "https://auctiondata.iaai.com/Search/SearchPlugin/Index"
IAAI_ACCESS_KEY = os.environ.get(
    "IAAI_ACCESS_KEY",
    "",
)

# IAA Express Search gives branch names, not coordinates. Unknown branches are
# skipped so the 250-mile rule stays strict.
IAAI_BRANCH_COORDS = {
    "ABILENE": (32.4487, -99.7331),
    "AUSTIN": (30.2672, -97.7431),
    "DALLAS": (32.7767, -96.7970),
    "DALLAS FT WORTH": (0.0000, 0.0000),
    "FT WORTH": (32.7555, -97.3308),
    "HOUSTON": (29.7604, -95.3698),
    "HOUSTON NORTH": (30.0080, -95.4900),
    "HOUSTON SOUTH": (29.6000, -95.2500),
    "LONGVIEW": (32.5007, -94.7405),
    "OKLAHOMA CITY": (35.4676, -97.5164),
    "SAN ANTONIO": (29.4241, -98.4936),
    "SAN ANTONIO SOUTH": (29.3000, -98.5000),
    "SHREVEPORT": (32.5252, -93.7502),
    "TULSA": (36.1540, -95.9928),
    "WACO": (31.5493, -97.1467),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
}


def haversine_mi(a, b):
    R = 3958.8
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def request_text(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def request_json(url):
    return json.loads(request_text(url))


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _words(value):
    return re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()


def _model_allowed(text):
    model = _words(text)
    if re.search(r"\b[34] SERIES\b", model):
        return True
    if re.search(r"\bM[34]\b", model):
        return True
    return any(re.search(rf"\b{tag}[A-Z0-9]*\b", model)
               for tag in MODEL_NUMBERS)


def _is_hybrid_or_ev(text, fuel=""):
    haystack = _words(f"{text} {fuel}")
    compact = haystack.replace(" ", "")
    if "HYBRID" in haystack or "ELECTRIC" in haystack:
        return True
    if "330E" in compact or "430E" in compact:
        return True
    return bool(re.search(r"\b(I3|I4|I8|IX)\b", haystack))


def _clean_title(title, clean_flag=False):
    if clean_flag:
        return True
    t = (title or "").upper()
    bad = ("SALVAGE", "REBUILT", "SCRAP", "JUNK", "DISMANTLER", "PARTS",
           "FLOOD", "NON-REPAIRABLE", "CERTIFICATE OF DESTRUCTION",
           "BILL OF SALE")
    if any(word in t for word in bad):
        return False
    good = ("CLEAN", "CLEAR", "ORIGINAL", "CERTIFICATE OF TITLE",
            "CERT OF TITLE")
    return any(word in t for word in good)


def matches(lot):
    if not lot["id"] or lot["id"] == "None":
        return False
    if lot["year"] < YEAR_MIN:
        return False
    # Allow odometer=0 only when the source explicitly marks it unknown
    # (AutoBidMaster's "Not Actual" / "Exempt" brands). User reviews manually.
    if lot["odometer"] > MAX_ODO:
        return False
    if not lot["odometer"] and not lot.get("odometer_unknown"):
        return False
    if not _model_allowed(lot["model"]):
        return False
    if _is_hybrid_or_ev(lot["model"], lot.get("fuel", "")):
        return False
    if not _clean_title(lot["title"], lot.get("title_clean", False)):
        return False
    if any(b in lot["damage"].upper() for b in DAMAGE_BLOCK):
        return False
    if lot["source"] == "iaai" and lot["distance_mi"] is None:
        return False
    if lot["distance_mi"] is not None and lot["distance_mi"] > MAX_MILES:
        return False
    return True


def fetch_copart():
    lots = []
    page = 1
    max_pages = 1
    while page <= max_pages:
        params = {
            "search_path": ABM_SEARCH_PATH,
            "page": page,
            "size": 100,
            "sort": "sale_date",
            "order": "asc",
        }
        url = ABM_SEARCH_URL + "?" + urllib.parse.urlencode(params, safe=",")
        data = request_json(url)
        lots.extend(normalize_copart(item) for item in data.get("lots", []))
        max_pages = min(_int(data.get("query", {}).get("maxNumberOfPages")) or 1, 20)
        page += 1
    return lots


def normalize_copart(item):
    title = item.get("title") or {}
    loc = item.get("location") or item.get("saleLocation") or {}
    lot_no = item.get("lotNumber") or item.get("id")
    model = " ".join(filter(None, [
        item.get("description"),
        item.get("model"),
        item.get("modelGroup"),
    ]))
    title_text = " ".join(filter(None, [
        title.get("stateCode"),
        title.get("name"),
        title.get("categoryName"),
    ]))
    damage = " ".join(filter(None, [
        item.get("primaryDamage"),
        item.get("secondaryDamage"),
    ]))
    link = item.get("link") or ""
    odo = _int(item.get("odometer"))
    odo_brand = (item.get("odometerBrand") or "").upper()
    odo_unknown = odo == 0 and odo_brand in ("NOT ACTUAL", "EXEMPT")
    return {
        "id": str(lot_no),
        "source": "copart",
        "year": _int(item.get("year")),
        "model": model,
        "odometer": odo,
        "odometer_unknown": odo_unknown,
        "title": title_text,
        "title_clean": title.get("category") == "C",
        "damage": damage,
        "yard": item.get("locationName") or loc.get("name"),
        "distance_mi": None,  # AutoBidMaster filtered server-side by radius.
        "bid": item.get("currentBid") or item.get("highBid") or 0,
        "buy_now": item.get("buyItNow") or 0,
        "sale_date": item.get("saleStartAt") or item.get("saleDate"),
        "url": urllib.parse.urljoin(ABM_BASE, link),
        "fuel": item.get("fuel") or "",
    }


class IAAIRowParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.div_depth = 0
        self.in_h4 = False
        self.in_title_link = False
        self.in_label = False
        self.title = []
        self.label = []
        self.value = []
        self.current_label = None
        self.value_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            classes = (attrs.get("class") or "").split()
            if self.row is None and "list" in classes and "col-md-12" in classes:
                self.row = {"fields": {}, "url": ""}
                self.div_depth = 1
                self.title = []
                return
            if self.row is not None:
                self.div_depth += 1
        if self.row is None:
            return
        if tag == "h4":
            self.in_h4 = True
        elif tag == "a" and self.in_h4:
            self.in_title_link = True
            self.row["url"] = attrs.get("href") or ""
        elif tag == "label":
            self.in_label = True
            self.label = []
            self.value = []
            self.value_depth = self.div_depth

    def handle_data(self, data):
        if self.row is None:
            return
        if self.in_label:
            self.label.append(data)
        elif self.in_title_link:
            self.title.append(data)
        elif self.current_label:
            self.value.append(data)

    def handle_endtag(self, tag):
        if self.row is None:
            return
        if tag == "a" and self.in_title_link:
            self.in_title_link = False
        elif tag == "h4":
            self.in_h4 = False
        elif tag == "label":
            raw = " ".join(self.label).strip().rstrip(":").lower()
            self.current_label = re.sub(r"\s+", " ", raw)
            self.in_label = False
        elif tag == "div":
            if self.current_label and self.div_depth == self.value_depth:
                value = " ".join(self.value)
                value = re.sub(r"\s+", " ", value).strip()
                self.row["fields"][self.current_label] = value
                self.current_label = None
                self.value = []
            self.div_depth -= 1
            if self.div_depth == 0:
                title = re.sub(r"\s+", " ", " ".join(self.title)).strip()
                if title:
                    self.row["title"] = title
                    self.rows.append(self.row)
                self.row = None


def parse_iaai_rows(html):
    parser = IAAIRowParser()
    parser.feed(html)
    return parser.rows


def iaai_search_url(keyword):
    params = {
        "buynow": "False",
        "rundrv": "False",
        "keyword": keyword,
        "filter": f"{{YearFilter:{YEAR_MIN}-{YEAR_MAX}}}",
        "Language": "en-US",
        "timezone": "120",
        "AccessKey": IAAI_ACCESS_KEY,
    }
    return IAAI_SEARCH_URL + "?" + urllib.parse.urlencode(
        params, safe="{}:-_"
    )


def fetch_iaai():
    lots = []
    seen = set()
    for tag in (*MODEL_NUMBERS, "M3", "M4"):
        html = request_text(iaai_search_url(f"BMW {tag}"))
        for row in parse_iaai_rows(html):
            lot = normalize_iaai(row)
            if lot["id"] in seen:
                continue
            seen.add(lot["id"])
            lots.append(lot)
    return lots


def parse_odometer(text):
    m = re.search(r"([\d,]+)\s*(MI|MILES|KM)", (text or "").upper())
    if not m:
        return 0
    value = int(m.group(1).replace(",", ""))
    if m.group(2) == "KM":
        return round(value * 0.621371)
    return value


def branch_distance(branch):
    key = _words(branch or "")
    for name, coord in IAAI_BRANCH_COORDS.items():
        if name in key:
            return haversine_mi(ORIGIN, coord)
    return None


def normalize_iaai(row):
    fields = row.get("fields", {})
    title = row.get("title", "")
    year_match = re.search(r"\b(20\d{2}|19\d{2})\b", title)
    branch = fields.get("branch", "")
    return {
        "id": fields.get("stock#", ""),
        "source": "iaai",
        "year": _int(year_match.group(1) if year_match else 0),
        "model": title,
        "odometer": parse_odometer(fields.get("odometer", "")),
        "title": fields.get("sale document", ""),
        "title_clean": False,
        "damage": fields.get("loss type", ""),
        "yard": branch,
        "distance_mi": branch_distance(branch),
        "bid": "",
        "buy_now": "",
        "sale_date": fields.get("auction", ""),
        "url": row.get("url", ""),
        "fuel": "",
    }


def load_seen():
    if not STATE.exists():
        return set()
    return set(json.loads(STATE.read_text()).get("seen_ids", []))


def save_seen(seen):
    STATE.write_text(json.dumps({"seen_ids": sorted(seen)}, indent=2))


def main():
    json_mode = "--json" in sys.argv
    seen = load_seen()
    new_hits = []

    for label, fetch in [("copart", fetch_copart), ("iaai", fetch_iaai)]:
        try:
            items = fetch()
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:200]
            print(f"[{label}] HTTP {e.code}: {msg}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"[{label}] error: {e}", file=sys.stderr)
            continue
        if not json_mode:
            print(f"[{label}] {len(items)} raw items", file=sys.stderr)
        for lot in items:
            if not matches(lot):
                continue
            key = f"{lot['source']}:{lot['id']}"
            if key in seen:
                continue
            seen.add(key)
            new_hits.append(lot)

    save_seen(seen)

    sorted_hits = sorted(new_hits, key=lambda x: x["odometer"])

    if json_mode:
        json.dump({"new_count": len(sorted_hits), "matches": sorted_hits},
                  sys.stdout, default=str)
        sys.stdout.write("\n")
        return

    if not new_hits:
        print("No new matches.")
        return

    print(f"\n{len(new_hits)} new match(es):\n")
    for lot in sorted_hits:
        if lot["distance_mi"] is None:
            dist = "within 250mi"
        else:
            dist = f"{lot['distance_mi']:.0f}mi from ARL"
        price = f"bid=${lot['bid']}" if lot["bid"] not in ("", None) else "bid=--"
        if lot["buy_now"]:
            price += f" buy_now=${lot['buy_now']}"
        miles = "??mi" if lot.get("odometer_unknown") else f"{lot['odometer']:,}mi"
        print(f"  [{lot['source']}] {lot['year']} BMW {lot['model']} | "
              f"{miles} | {lot['yard']} ({dist}) | "
              f"title={lot['title']} | dmg={lot['damage'].strip()} | "
              f"{price} | {lot['url']}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Daily auction watcher matching configurable buy criteria.

Config lives in `auction.config.toml` next to this script (or override with
`--config PATH`). See `examples/` for ready-made configs for popular targets.

Prints only lots not seen in prior runs; state is `seen.json` next to the
config. Sources: Copart (via AutoBidMaster public JSON) + IAA (HTML scrape).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("Python 3.11+ required (tomllib stdlib). You have " + sys.version)


SCRIPT_DIR = Path(__file__).parent

ABM_SEARCH_URL = "https://www.autobidmaster.com/en/data/v2/inventory/search"
ABM_BASE = "https://www.autobidmaster.com"
IAAI_SEARCH_URL = "https://auctiondata.iaai.com/Search/SearchPlugin/Index"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
}

# IAA Express Search gives branch names, not coordinates. Unknown branches are
# skipped so the max-distance rule stays strict. Add your local branches here.
IAAI_BRANCH_COORDS = {
    "ABILENE": (32.4487, -99.7331),
    "ATLANTA": (33.7490, -84.3880),
    "AUSTIN": (30.2672, -97.7431),
    "BOSTON": (42.3601, -71.0589),
    "CHICAGO": (41.8781, -87.6298),
    "DALLAS": (32.7767, -96.7970),
    "DALLAS FT WORTH": (32.7357, -97.1081),
    "DENVER": (39.7392, -104.9903),
    "FT WORTH": (32.7555, -97.3308),
    "HOUSTON": (29.7604, -95.3698),
    "HOUSTON NORTH": (30.0080, -95.4900),
    "HOUSTON SOUTH": (29.6000, -95.2500),
    "LONGVIEW": (32.5007, -94.7405),
    "LOS ANGELES": (34.0522, -118.2437),
    "MIAMI": (25.7617, -80.1918),
    "NEW YORK": (40.7128, -74.0060),
    "OKLAHOMA CITY": (35.4676, -97.5164),
    "ORLANDO": (28.5383, -81.3792),
    "PHILADELPHIA": (39.9526, -75.1652),
    "PHOENIX": (33.4484, -112.0740),
    "PORTLAND": (45.5152, -122.6784),
    "SAN ANTONIO": (29.4241, -98.4936),
    "SAN ANTONIO SOUTH": (29.3000, -98.5000),
    "SEATTLE": (47.6062, -122.3321),
    "SHREVEPORT": (32.5252, -93.7502),
    "TULSA": (36.1540, -95.9928),
    "WACO": (31.5493, -97.1467),
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"config not found: {path}\nCopy one from examples/ to {path.name}.")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    # Required fields
    for section, key in (("vehicle", "make"), ("location", "origin"), ("location", "max_miles")):
        if key not in cfg.get(section, {}):
            sys.exit(f"config missing [{section}].{key}")

    # Defaults
    veh = cfg["vehicle"]
    veh.setdefault("models", [])
    veh.setdefault("model_numbers", [])
    veh.setdefault("model_regex", None)  # optional override regex
    veh.setdefault("exclude_patterns", [])

    flt = cfg.setdefault("filters", {})
    flt.setdefault("year_min", 2000)
    flt.setdefault("year_max", date.today().year + 1)
    flt.setdefault("max_odometer", 200000)
    flt.setdefault("allow_unknown_odometer", False)
    flt.setdefault("title_required_clean", True)
    flt.setdefault("title_block", [
        "SALVAGE", "REBUILT", "SCRAP", "JUNK", "DISMANTLER", "PARTS",
        "FLOOD", "NON-REPAIRABLE", "CERTIFICATE OF DESTRUCTION", "BILL OF SALE",
    ])
    flt.setdefault("title_clean", [
        "CLEAN", "CLEAR", "ORIGINAL", "CERTIFICATE OF TITLE", "CERT OF TITLE",
    ])
    flt.setdefault("damage_block", [
        "AIRBAG", "FLOOD", "WATER", "FIRE", "BURN", "ROLLOVER",
        "FRAME", "MECHANICAL", "ENGINE", "STRIPPED", "VANDALISM",
    ])

    cfg.setdefault("location", {}).setdefault("zip", "00000")
    return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _model_allowed(text, cfg):
    """Match against vehicle.models (series names) or vehicle.model_numbers (tags).

    `vehicle.model_regex` can override with a raw regex (matched against the
    upper-cased model field). Useful for nuanced filters.
    """
    veh = cfg["vehicle"]
    if veh.get("model_regex"):
        return bool(re.search(veh["model_regex"], text.upper()))

    model = _words(text)
    for series in veh.get("models", []):
        pat = r"\b" + re.escape(series.upper()) + r"\b"
        if re.search(pat, model):
            return True
    for tag in veh.get("model_numbers", []):
        if re.search(rf"\b{re.escape(tag.upper())}[A-Z0-9]*\b", model):
            return True
    return False


def _is_excluded(text, fuel, cfg):
    haystack = _words(f"{text} {fuel}")
    for pattern in cfg["vehicle"].get("exclude_patterns", []):
        if re.search(pattern, haystack, re.IGNORECASE):
            return True
    return False


def _clean_title(title, clean_flag, cfg):
    if clean_flag:
        return True
    t = (title or "").upper()
    flt = cfg["filters"]
    if any(word in t for word in flt["title_block"]):
        return False
    if not flt["title_required_clean"]:
        return True
    return any(word in t for word in flt["title_clean"])


def matches(lot, cfg):
    flt = cfg["filters"]
    if not lot["id"] or lot["id"] == "None":
        return False
    if lot["year"] < flt["year_min"] or lot["year"] > flt["year_max"]:
        return False
    if lot["odometer"] > flt["max_odometer"]:
        return False
    if not lot["odometer"] and not lot.get("odometer_unknown"):
        return False
    if lot["odometer_unknown"] and not flt["allow_unknown_odometer"]:
        return False
    if not _model_allowed(lot["model"], cfg):
        return False
    if _is_excluded(lot["model"], lot.get("fuel", ""), cfg):
        return False
    if not _clean_title(lot["title"], lot.get("title_clean", False), cfg):
        return False
    if any(b in lot["damage"].upper() for b in flt["damage_block"]):
        return False
    if lot["source"] == "iaai" and lot["distance_mi"] is None:
        return False
    if lot["distance_mi"] is not None and lot["distance_mi"] > cfg["location"]["max_miles"]:
        return False
    return True


# ---------------------------------------------------------------------------
# Copart (via AutoBidMaster)
# ---------------------------------------------------------------------------

def abm_search_path(cfg):
    veh = cfg["vehicle"]
    loc = cfg["location"]
    flt = cfg["filters"]
    make = veh["make"].lower().replace(" ", "-")
    lat, lon = loc["origin"]
    # AutoBidMaster distance code: 1=10mi 2=25mi 3=50mi 4=100mi 5=200mi 6=300mi
    miles = loc["max_miles"]
    dist_code = 6 if miles > 200 else 5 if miles > 100 else 4 if miles > 50 else 3 if miles > 25 else 2
    return (
        f"make-{make}/doc-type-c/odometer-0,{flt['max_odometer']}/"
        f"uorigin-{lat},{lon}/distance-{dist_code}"
    )


def fetch_copart(cfg):
    lots = []
    page = 1
    max_pages = 1
    search_path = abm_search_path(cfg)
    while page <= max_pages:
        params = {
            "search_path": search_path,
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
        item.get("description"), item.get("model"), item.get("modelGroup"),
    ]))
    title_text = " ".join(filter(None, [
        title.get("stateCode"), title.get("name"), title.get("categoryName"),
    ]))
    damage = " ".join(filter(None, [
        item.get("primaryDamage"), item.get("secondaryDamage"),
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
        "distance_mi": None,  # ABM filters server-side via uorigin
        "bid": item.get("currentBid") or item.get("highBid") or 0,
        "buy_now": item.get("buyItNow") or 0,
        "sale_date": item.get("saleStartAt") or item.get("saleDate"),
        "url": urllib.parse.urljoin(ABM_BASE, link),
        "fuel": item.get("fuel") or "",
    }


# ---------------------------------------------------------------------------
# IAA
# ---------------------------------------------------------------------------

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


def iaai_search_url(keyword, cfg):
    access_key = os.environ.get("IAAI_ACCESS_KEY", "")
    if not access_key:
        sys.exit("IAAI_ACCESS_KEY env var required. Get one free from auctiondata.iaai.com.")
    params = {
        "buynow": "False",
        "rundrv": "False",
        "keyword": keyword,
        "filter": f"{{YearFilter:{cfg['filters']['year_min']}-{cfg['filters']['year_max']}}}",
        "Language": "en-US",
        "timezone": "120",
        "AccessKey": access_key,
    }
    return IAAI_SEARCH_URL + "?" + urllib.parse.urlencode(params, safe="{}:-_")


def fetch_iaai(cfg):
    lots = []
    seen = set()
    make = cfg["vehicle"]["make"]
    tags = list(cfg["vehicle"].get("model_numbers") or []) + list(cfg["vehicle"].get("models") or [])
    if not tags:
        tags = [make]
        make = ""  # avoid duplicating "Make Make"
    for tag in tags:
        kw = f"{make} {tag}".strip()
        html = request_text(iaai_search_url(kw, cfg))
        for row in parse_iaai_rows(html):
            lot = normalize_iaai(row, cfg)
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


def branch_distance(branch, origin):
    key = _words(branch or "")
    for name, coord in IAAI_BRANCH_COORDS.items():
        if name in key:
            return haversine_mi(origin, coord)
    return None


def normalize_iaai(row, cfg):
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
        "odometer_unknown": False,
        "title": fields.get("sale document", ""),
        "title_clean": False,
        "damage": fields.get("loss type", ""),
        "yard": branch,
        "distance_mi": branch_distance(branch, tuple(cfg["location"]["origin"])),
        "bid": "",
        "buy_now": "",
        "sale_date": fields.get("auction", ""),
        "url": row.get("url", ""),
        "fuel": "",
    }


# ---------------------------------------------------------------------------
# State + main
# ---------------------------------------------------------------------------

def load_seen(state_path):
    if not state_path.exists():
        return set()
    return set(json.loads(state_path.read_text()).get("seen_ids", []))


def save_seen(state_path, seen):
    state_path.write_text(json.dumps({"seen_ids": sorted(seen)}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(SCRIPT_DIR / "auction.config.toml"),
                        help="Path to config TOML (default: auction.config.toml next to script).")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON instead of human text.")
    args = parser.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = load_config(cfg_path)
    state_path = cfg_path.parent / "seen.json"
    seen = load_seen(state_path)
    new_hits = []

    sources = [
        ("copart", lambda: fetch_copart(cfg)),
        ("iaai", lambda: fetch_iaai(cfg)),
    ]
    for label, fetch in sources:
        try:
            items = fetch()
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:200]
            print(f"[{label}] HTTP {e.code}: {msg}", file=sys.stderr)
            continue
        except SystemExit:
            raise
        except Exception as e:
            print(f"[{label}] error: {e}", file=sys.stderr)
            continue
        if not args.json:
            print(f"[{label}] {len(items)} raw items", file=sys.stderr)
        for lot in items:
            if not matches(lot, cfg):
                continue
            key = f"{lot['source']}:{lot['id']}"
            if key in seen:
                continue
            seen.add(key)
            new_hits.append(lot)

    save_seen(state_path, seen)

    sorted_hits = sorted(new_hits, key=lambda x: x["odometer"])

    if args.json:
        json.dump({"new_count": len(sorted_hits), "matches": sorted_hits},
                  sys.stdout, default=str)
        sys.stdout.write("\n")
        return

    if not new_hits:
        print("No new matches.")
        return

    name = cfg["vehicle"]["make"].title()
    home_label = cfg["location"].get("home_label", "home")
    print(f"\n{len(new_hits)} new {name} match(es):\n")
    for lot in sorted_hits:
        if lot["distance_mi"] is None:
            dist = f"within {cfg['location']['max_miles']}mi"
        else:
            dist = f"{lot['distance_mi']:.0f}mi from {home_label}"
        price = f"bid=${lot['bid']}" if lot["bid"] not in ("", None) else "bid=--"
        if lot["buy_now"]:
            price += f" buy_now=${lot['buy_now']}"
        miles = "??mi" if lot.get("odometer_unknown") else f"{lot['odometer']:,}mi"
        print(f"  [{lot['source']}] {lot['year']} {lot['model']} | "
              f"{miles} | {lot['yard']} ({dist}) | "
              f"title={lot['title']} | dmg={lot['damage'].strip()} | "
              f"{price} | {lot['url']}")


if __name__ == "__main__":
    main()

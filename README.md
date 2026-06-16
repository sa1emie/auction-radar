# BMW Auction Watcher

Daily scan of Copart (via AutoBidMaster) and IAA for BMW 3-Series and 4-Series listings matching strict buy criteria. Only prints *new* lots — state persists between runs in `seen.json`. Designed for a single, recurring shell run via cron or a launchd agent.

## Why

Buying salvage-title performance cars is a numbers game: hundreds of irrelevant listings to one realistic candidate. Manual checking is unsustainable. This narrows two large auction houses down to the few lots that match your hard filters — make, model, mileage, distance, title status, damage type — and only surfaces things you haven't already passed on.

## What it filters

- **Make/model:** BMW 3 and 4 Series (320 / 328 / 330 / 335 / 340 / M3 / 420 / 428 / 430 / 435 / 440 / M4).
- **Years:** 2018 onward.
- **Odometer:** ≤ 90,000 mi (unknown odometer allowed only when explicitly branded "Not Actual" / "Exempt", flagged for manual review).
- **Distance:** ≤ 250 mi from a configurable origin lat/long.
- **Title:** must be clean / clear / original — strips salvage, rebuilt, parts, junk, flood, non-repairable, certificate-of-destruction.
- **Damage:** blocks airbag, flood, water, fire, burn, rollover, frame, mechanical, engine, stripped, vandalism. Hail and undercarriage are allowed (cheap to fix).
- **Excludes hybrids and EVs** (330e, 430e, i3, i4, i8, iX) — drivetrain priorities don't match the buyer.

## Usage

```bash
# One-shot scan, prints new matches:
python3 auction.py

# Machine-readable JSON for downstream tools (e.g. a desktop widget):
python3 auction.py --json
```

`seen.json` is created next to the script on first run. Delete it to re-surface previously-seen lots.

### Config you'll need to change

Open `auction.py` and set:
- `ZIP`, `ORIGIN` — your home ZIP + lat/long
- `MAX_MILES`, `MAX_ODO`, `YEAR_MIN` — your tolerances
- `MODEL_NUMBERS`, `DAMAGE_BLOCK` — if you care about something other than BMW 3/4 series

IAA also requires an `IAAI_ACCESS_KEY` (free from auctiondata.iaai.com). Set it via env var:

```bash
export IAAI_ACCESS_KEY="your-key-here"
```

## Output format

Human mode prints one line per new lot:

```
[copart] 2020 BMW 330i xDrive | 38,421mi | DALLAS (28mi from ARL) | title=TX CERTIFICATE OF TITLE | dmg=FRONT END | bid=$4500 buy_now=$9800 | https://...
```

JSON mode emits `{"new_count": N, "matches": [...]}` — see `--json`.

## Implementation notes

- Pure stdlib (`urllib.request`, `html.parser`, `re`, `json`, `math`, `pathlib`) — zero install footprint beyond Python 3.10+.
- AutoBidMaster has a JSON search endpoint with server-side radius filtering (cheap).
- IAA's Express Search only returns HTML and only gives branch *names* (not coordinates), so the script maps known branch names → coords for a `haversine_mi` distance check. Unknown branches are skipped to keep the 250-mile rule strict.
- All filters live in `matches()` — single function, easy to fork for a different make.

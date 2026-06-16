# auction-radar

Daily watcher for Copart (via AutoBidMaster) and IAA salvage auctions. Configure it for any car: make, model, year, mileage, distance, title, and damage filters. It only surfaces lots it hasn't seen before, so a daily cron run gives you just the new candidates instead of the same list every morning.

## Why

Buying a salvage-title car means scanning hundreds of listings to find the one that's actually worth a bid, across two auction houses, every day. This does that scan for you. It keeps only the lots that pass your filters, and it remembers what you've already passed on, so each morning's output is just what's new.

## Quickstart

```bash
# Need Python 3.11+ (uses stdlib tomllib for config)
git clone https://github.com/sa1emie/auction-radar.git
cd auction-radar

# Pick a starting config from examples/ and copy it as your active config:
cp examples/bmw-3-4-series.toml auction.config.toml

# Edit auction.config.toml — set your location.origin, location.max_miles, and any filter tweaks
# Then:
python3 auction.py
```

For JSON output (e.g. a desktop widget consuming it):
```bash
python3 auction.py --json
```

For a different config:
```bash
python3 auction.py --config ~/myconfigs/honda-civic.toml
```

## Configs

Three examples ship in `examples/`:

| File | Target | Notes |
|---|---|---|
| `bmw-3-4-series.toml` | BMW 3 / 4 Series + M3 / M4 | Excludes hybrids (xxxe) + i-series EVs |
| `honda-civic.toml` | Honda Civic (all trims incl. Si / Type R) | Tighter 150mi radius, no exclusions |
| `tesla-model-3.toml` | Tesla Model 3 | Allows unknown odometer; blocks ELECTRICAL / BATTERY damage (battery fires = total loss) |

### Config schema

```toml
[location]
zip = "00000"                  # informational
origin = [0.0000, 0.0000]      # [lat, lon] — used for distance + Copart radius
max_miles = 250                # max distance from origin
home_label = "home"            # short tag printed in distance output

[vehicle]
make = "BMW"                   # required
models = ["3 Series", "M3"]    # match these series names in lot title (regex \bword\b)
model_numbers = ["320", "330"] # match these tags (regex \btag[A-Z0-9]*\b)
model_regex = ""               # OR provide a raw regex to override the above
exclude_patterns = [           # reject if any matches (case-insensitive)
    "\\bHYBRID\\b",
    "\\bELECTRIC\\b",
]

[filters]
year_min = 2018
year_max = 2027                # defaults to current year + 1
max_odometer = 90000
allow_unknown_odometer = true  # Copart NOT ACTUAL / EXEMPT brand
title_required_clean = true    # rejects salvage / rebuilt / parts / flood
# Override the built-in blocklists if needed:
# title_block = [...]
# title_clean = [...]
# damage_block = [...]
```

## IAA setup

IAA requires a free access key. Get one from [auctiondata.iaai.com](https://auctiondata.iaai.com/), then:

```bash
export IAAI_ACCESS_KEY="your-key-here"
```

(Add to your shell profile so cron picks it up.)

## Cron / daily run

A typical setup runs daily at, say, 7 AM. Append your local crontab:

```cron
0 7 * * * cd $HOME/auction-radar && IAAI_ACCESS_KEY=... /usr/bin/python3 auction.py
```

Or on macOS launchd, mirror the structure in `[sentinel](https://github.com/sa1emie/sentinel)`'s `scripts/install-launchd.sh`.

## How it works

- **Copart** via [AutoBidMaster](https://www.autobidmaster.com)'s public JSON search endpoint. Filters server-side by `make`, `max_odometer`, `origin` lat/lon, and `max_miles` (mapped to the closest distance code: 25 / 50 / 100 / 200 / 300 mi).
- **IAA** via the Express Search HTML endpoint, parsed with `html.parser`. IAA only returns branch *names* (not coords), so the script maps a hardcoded set of known branch names → coordinates for a Haversine distance check. Unknown branches are skipped to keep the distance rule strict. Add yours to `IAAI_BRANCH_COORDS` in `auction.py`.
- All filters live in `matches()`. One function, easy to fork for an unsupported field.

## Output

Human mode prints one line per new lot:

```
[copart] 2020 BMW 330i xDrive | 38,421mi | DALLAS (28mi from home) | title=TX CERTIFICATE OF TITLE | dmg=FRONT END | bid=$4500 buy_now=$9800 | https://...
```

JSON mode (`--json`) emits:

```json
{
  "new_count": 3,
  "matches": [
    { "id": "...", "source": "copart", "year": 2020, "model": "...", ... }
  ]
}
```

## Implementation notes

- Pure Python stdlib (`urllib.request`, `html.parser`, `re`, `json`, `math`, `tomllib`, `argparse`). Zero install footprint beyond Python 3.11+.
- State persists in `seen.json` next to the active config file. Delete it to re-surface previously-seen lots.
- Each fetch is independently wrapped in `try/except` — one source down doesn't kill the run.

## License

MIT.

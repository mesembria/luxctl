# luxctl

Sensor-driven lighting control. Currently in Phase 0 (observation only —
records data, commands nothing).

See `docs/superpowers/specs/2026-07-21-luxctl-design.md` for the design.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,analysis]"
cp sensors.toml.example sensors.toml
# edit sensors.toml with your HA entity IDs
export LUXCTL_HA_TOKEN="<long-lived token from HA>"
```

The token is read from the environment only. It must never be written into
`sensors.toml` or any other file in this repository.

## Run

```bash
.venv/bin/luxctl --config sensors.toml --db data/luxctl.db
```

Run it in the foreground first. On startup it logs how many entities it seeded
from Home Assistant's current state, and warns by name about any configured
sensor or light that Home Assistant did not return — that warning is how a
typo'd entity ID is caught before it costs two weeks of data.

## Test

```bash
.venv/bin/python -m pytest -v
```

## Analyse

```bash
.venv/bin/python analysis/correlate.py --db data/luxctl.db --out analysis/out
```

Read the coverage table before the correlations. A sensor with low completeness
had outages, and its correlation figures are not trustworthy.

## Phase 0 checklist

- [ ] Nodes sited, all sensors reporting
- [ ] Service running under systemd
- [ ] Data flowing (`sqlite3 data/luxctl.db "SELECT COUNT(*) FROM readings"`)
- [ ] `journalctl -u luxctl` checked every few days for staleness warnings
- [ ] Two weeks elapsed
- [ ] Correlation analysis run and interpreted

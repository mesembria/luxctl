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

## Deployment

The token goes in `/etc/luxctl.env`, outside the repository. Never in
`sensors.toml`, never in the unit file, never anywhere under this checkout.

```bash
git clone <repo> /home/pi/luxctl
cd /home/pi/luxctl
python3 -m venv .venv
.venv/bin/pip install -e .
cp sensors.toml.example sensors.toml     # then edit in your entity IDs
```

Run it in the foreground once before installing the service, and check the
seeded count and any "not found in Home Assistant" warnings:

```bash
export LUXCTL_HA_TOKEN="<token>"
.venv/bin/luxctl --config sensors.toml --db data/luxctl.db --verbose
```

Then install:

```bash
printf 'LUXCTL_HA_TOKEN=%s\n' "<token>" | sudo tee /etc/luxctl.env >/dev/null
sudo chmod 600 /etc/luxctl.env
sudo cp deploy/luxctl.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now luxctl
systemctl status luxctl
```

Verify data is accumulating before starting the two-week clock:

```bash
sqlite3 data/luxctl.db \
  "SELECT sensor_id, COUNT(*), MIN(at), MAX(at) FROM readings GROUP BY sensor_id"
```

One row per configured sensor, counts climbing. A sensor missing from that
output is not reporting — fix it now, not in a fortnight.

## Phase 0 checklist

- [ ] Nodes sited, all sensors reporting
- [ ] Service running under systemd
- [ ] Data flowing (`sqlite3 data/luxctl.db "SELECT COUNT(*) FROM readings"`)
- [ ] `journalctl -u luxctl` checked every few days for staleness warnings
- [ ] Two weeks elapsed
- [ ] Correlation analysis run and interpreted

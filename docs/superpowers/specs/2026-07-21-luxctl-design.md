# luxctl — Sensor-Driven Lighting Control

**Date:** 2026-07-21
**Status:** Design approved, not yet implemented

## Problem

The house has smart bulbs across three vendors (Hue, Meross, Govee), controlled
manually through Apple Home. Lighting does not respond to actual light levels —
on an overcast afternoon the rooms stay dim until someone notices and reaches
for a phone.

The goal is lighting that responds to real conditions. The complication is that
nobody yet knows which control approach works in this house, so the system must
be built to answer that question empirically rather than to implement a guess.

## Why not Apple Home

HomeKit is programmable only from on-device Apple code — a native app holding
the HomeKit entitlement. There is no cloud REST API, no server-side SDK, nothing
reachable from a script on a Raspberry Pi. The Home app's automation UI does not
support numeric light-level thresholds as triggers.

Apple Home therefore remains the household's manual interface, and the control
loop lives outside it. Home Assistant's HomeKit Bridge exposes everything back
into Apple Home, so the family experience is unchanged.

> Verify before implementation: Apple ships HomeKit changes each autumn, and
> this analysis reflects knowledge current to January 2026. Confirm the state of
> Home app automation triggers before Phase 0.

## Architecture

```
  QT Py + VEML7700 ─ESPHome┐
  Hue motion sensors ─────┤
  Hue bulbs ──────────────┼──► Home Assistant ──WebSocket──► luxctl
  Meross bulbs ───────────┤       (Raspberry Pi)            (Python)
  Govee bulbs ────────────┘                                    │
                                                               ▼
                                                            SQLite
```

Home Assistant is a **device driver layer and nothing more**. It speaks Zigbee,
Meross-local, Govee-LAN and Matter so that luxctl never implements a device
protocol. It holds no automation logic for this project.

luxctl is a Python service in a git repository. It subscribes to HA's event
stream, maintains a picture of the house, and issues light commands.

### The rule the design rests on

> Strategies are pure functions: `strategy(snapshot) -> list[LightCommand]`.
> No I/O, no clock access, no HA. Every input arrives as an argument.

This constraint buys three things the project cannot proceed without:

- **Testability** — construct a snapshot, assert on commands. No hardware.
- **Shadow mode** — call every strategy, dispatch only one. Free by construction.
- **Replay** — feed recorded snapshots to any strategy, offline. Evaluate a new
  idea against six weeks of real weather in about a second.

Almost every other decision follows from protecting this rule. Wanting to call
HA from inside a strategy is the signal that something belongs in the snapshot.

### Two halves

**Impure shell** — HA client, recorder, dispatcher, rate limiter. Knows about
sockets, clocks and disks. Deliberately boring; ideally contains no logic worth
testing.

**Pure core** — snapshot construction, calibration model, daylight estimation,
and every strategy. Knows nothing of the outside world. All the interesting code
lives here, and all of it is testable without a Raspberry Pi.

The service is stateless across restarts except for SQLite. It rebuilds its
picture from HA's current state plus recorded history.

## The feedback problem

A sensor in a room reads the light the bulbs are producing. A controller that
adjusts bulbs based on that reading is a closed loop and can hunt, pulse, or
drive itself to the rails.

Scheduling does not by itself avoid this. `brightness = schedule(t) × f(lux)`
re-evaluated every 30 seconds is a closed loop wearing a schedule. Three designs
genuinely break the loop:

1. **Sensor triggers transitions, does not regulate.** Model the day as a
   forward-only state machine (`day → evening → night`). Lux crossing a
   threshold advances the state; the state determines the lighting. Once in
   `evening`, the sensor has no further say. A state machine that only moves
   forward cannot oscillate. A time floor, or a requirement for a large reverse
   excursion, handles the genuine day-brightens-again case.

2. **Subtract what was commanded.** The controller knows what brightness it set.
   Calibrate once, after dark, by sweeping each light group and recording lux
   contribution at each sensor. Then:

   ```
   daylight_estimate = measured_lux − Σ (kᵢ × commanded_brightnessᵢ)
   ```

   The input no longer moves when the lights move. This is the rigorous fix. It
   degrades gracefully: drift produces a noisy estimate, not instability.

3. **Deny the sensor sight of the bulbs.** Place it in a window recess facing
   outward. It then measures the disturbance (daylight) rather than the result
   (room brightness) — feedforward control, with no feedback term to
   destabilise. Costs nothing but placement.

Design 3 implies a different sensor deployment entirely: a small number of
daylight references rather than one sensor per room. **Phase 0 tests whether
that is sufficient**, by correlating window sensors against room sensors.

## Data model

```python
@dataclass(frozen=True)
class SensorReading:
    sensor_id: str
    lux: float
    at: datetime

@dataclass(frozen=True)
class LightState:
    light_id: str
    on: bool
    brightness: float          # 0.0–1.0, normalized across vendors
    color_temp_k: int | None

@dataclass(frozen=True)
class Snapshot:
    at: datetime
    sun_elevation: float
    latest: Mapping[str, float]                       # lux per sensor
    history: Mapping[str, Sequence[SensorReading]]    # rolling window
    lights: Mapping[str, LightState]
    occupancy: Mapping[str, bool]
    daylight: Mapping[str, float]                     # calibration-corrected
    last_override: Mapping[str, datetime | None]      # per room

@dataclass(frozen=True)
class LightCommand:
    light_id: str
    on: bool
    brightness: float | None
    transition_s: float
    reason: str      # "evening ramp: daylight 120lx below 200lx threshold"
```

**Normalized brightness.** Hue is 0–100, HA is 0–255, Govee is 0–100. Hue's
scale is perceptual, not linear in lumens, which matters because the calibration
model predicts lux — a physical quantity. The adapter normalizes to 0.0–1.0 on
the way in; the calibration model fits whatever curve the data shows rather than
assuming linearity.

**`reason` is required, not decorative.** When the lights do something baffling
at 4pm on a Tuesday six weeks from now, a logged sentence explaining the
decision is worth more than any dashboard.

## Module layout

```
luxctl/
  shell/          # impure — sockets, clocks, disks
    ha_client.py      WebSocket, entity subscription, service calls
    recorder.py       append-only writes to SQLite
    dispatcher.py     rate limiting, deadband, sending commands
    runner.py         the tick loop
  core/           # pure — no I/O
    snapshot.py       assembles Snapshot from raw inputs
    calibration.py    bulb→sensor contribution model, fit + predict
    daylight.py       measured − predicted contribution
    safety.py         policy predicates (quiet hours, override backoff)
    strategies/
      null.py            baseline: does nothing. the control group
      threshold.py       lux threshold, on/off with hysteresis
      state_machine.py   forward-only day/evening/night transitions
      corrected.py       schedule × daylight estimate
      closed_loop.py     continuous regulation to a target lux
  replay.py       offline: recorded history → strategy → metrics
  calibrate.py    the nighttime sweep
  rooms.toml
```

`NullStrategy` earns its place: without a baseline override rate, "three
overrides a day" means nothing.

## Configuration

Rooms are declarative and version-controlled, so an experiment is a diff:

```toml
[rooms.living_room]
sensors  = ["living_room_lux", "south_window_lux"]
lights   = ["hue_living_1", "hue_living_2", "govee_strip"]
strategy = "corrected"
shadow   = ["threshold", "state_machine", "closed_loop", "null"]

[rooms.living_room.params]
target_lux           = 200
evening_trigger_lux  = 120
```

Because each room names its own live strategy, different rooms can run different
strategies simultaneously — a real A/B test under identical weather, otherwise
the hardest thing to obtain. Rooms are not perfectly comparable, so rotate
assignments periodically rather than trusting a single week.

## Safety

The dispatcher enforces a fixed ladder. Every strategy passes through all of it;
no strategy can opt out.

1. **Global kill switch** — an `input_boolean` in HA, exposed to Apple Home, so
   anyone in the house can stop the system from their phone without knowing what
   a Raspberry Pi is. Checked every tick.
2. **Quiet hours** — no commands 23:00–06:00 unless a room opts in.
3. **Override backoff** — a human touched a room's lights → hands off that room
   for 30 minutes. The single most important rule for the system being tolerated.
4. **Deadband** — discard commands changing brightness by less than ~3%.
5. **Max ramp rate** — brightness cannot move faster than a configured %/minute
   outside explicit transitions. Also contains a buggy strategy.
6. **Per-vendor rate limits** — the Hue bridge caps around 10 commands/sec
   aggregate and ~1/sec per group; Govee's cloud path is far tighter. Per-light
   minimum intervals, configured, enforced centrally.
7. **Clamps** — brightness stays within per-room min/max.
8. **Never turn lights on unprompted in an unoccupied room at night.** Lights
   coming on by themselves is the most alarming failure mode; getting it wrong
   once costs a month of household goodwill.

**Override detection uses HA context IDs, not heuristics.** Every service call
luxctl makes carries a context, echoed back on the resulting `state_changed`
event. A change arriving with a context luxctl did not originate is by
definition a human. Timing-and-value matching would misfire constantly during
transitions.

## Failure modes

| Failure | Response |
|---|---|
| Sensor stale (battery, reboot, WiFi) | Readings older than a configured staleness window (default 10 minutes) are marked stale. A room with all sensors stale falls back to schedule-only or does nothing. Never act on a frozen value. |
| Sensor garbage (spikes, zeros) | Median filter over the rolling window; reject physically implausible jumps. |
| HA down or restarting | Reconnect with backoff. Full state resync before dispatching. Never act on a partial picture. |
| luxctl crashes | Lights stay where they are. No dead-man's switch — inaction is less alarming than a house going dark. systemd restarts it. |
| Govee cloud fails | Log, do not retry aggressively. Those lights degrade to manual-only; the rest of the house is unaffected. |
| A strategy throws | Caught per-strategy, logged, skipped for that tick. A live strategy throwing repeatedly is auto-demoted to shadow. One bad experiment must not take down the loop. |

Sensor staleness is the failure that will actually recur, and the one that
produces the strangest behaviour if unhandled — a controller regulating happily
against a lux value frozen since Tuesday.

**Free health check:** the daylight estimate is `measured − predicted`. A
negative value is physically impossible and therefore unambiguous evidence that
calibration coefficients have drifted — blinds moved, furniture rearranged, a
bulb replaced, the season changed. Sustained negativity means recalibrate. Four
lines of code, and it catches the slow silent failure that would otherwise take
months to notice.

## Hardware

Keep the two existing Hue Motion Sensors — already integrated, and they supply
occupancy for free.

For additional sensors: the Hue Bridge only pairs Hue and Friends-of-Hue
certified devices, so any cheaper Zigbee sensor requires a second coordinator.
Rather than add a second radio, new sensors are **WiFi nodes running ESPHome**,
which report into HA directly and can sample at any rate.

Avoid Aqara *motion* sensors for this purpose: they report illuminance bundled
with a motion event, so an empty room stops reporting light levels precisely
when the reading matters. The dedicated Aqara Light Sensor T1 does not have this
problem, and remains a reasonable fallback for any location that later proves to
need battery power.

### Node design

| Part | Item | ~Cost |
|---|---|---|
| MCU | Adafruit QT Py ESP32-S2 | $12.50 |
| Sensor | Adafruit VEML7700 STEMMA QT breakout (4162) | $6 |
| Link | STEMMA QT cable, 50–500mm | $1 |
| Power | USB-C | — |

**No soldering and no breadboard.** Both boards carry STEMMA QT connectors, so a
node is three parts plugged together and flashed. Building the full set is an
afternoon rather than a project, and a node can be rebuilt or re-sited in
minutes — which matters, because placement is expected to change repeatedly.

The QT Py is roughly 22×18mm, small enough to hide, and the STEMMA QT cable
separates sensor from MCU for free. A location needing the ESP32 concealed just
takes a longer cable — Adafruit's run to 500mm, SparkFun's Qwiic to 1m, and they
chain. Beyond ~1m an LTC4311 I²C extender (~$3) restores margin. No decision on
this is required up front; Phase 0 reveals which locations need it.

### Why VEML7700 and not BH1750

The measurement span is roughly five orders of magnitude: a dim room at night is
2–10 lx, a lit room 100–300 lx, an overcast window 1,000–3,000 lx, direct sun
50,000–100,000 lx.

| | BH1750 | VEML7700 |
|---|---|---|
| Range | 1 – 65,535 lx | 0 – 120,000 lx |
| Low-end resolution | 1 lx | ~0.0036 lx at max gain |
| Gain control | manual MTreg | auto gain + integration time |
| IR rejection | poor | filtered, near eye response |

BH1750 fails at the bottom of that span, where one count of noise on a 6 lx
reading is a 17% error — and the bottom is where dusk transitions and
"is this room too dim" decisions live.

IR rejection matters for the calibration model specifically. Sunlight, LED and
halogen differ sharply in IR content, so a poorly filtered sensor reports
different lux for the same perceived brightness depending on source. That error
lands directly in the bulb-contribution coefficients.

**Use the same sensor IC at every location.** Phase 0's deliverable is a
window-versus-room correlation; mixing ICs contaminates it with spectral
response differences.

**Fit a diffuser.** A bare sensor facing a window produces a spot reading
dominated by whether the sun is currently in its field of view — step changes
that are noise, not signal. A white diffuser (PTFE cap, sanded acrylic, half a
ping-pong ball) approximates a cosine-corrected reading of overall sky
brightness.

The QT Py ESP32-S2 is single-core at 240MHz with 4MB flash and 2MB PSRAM, and
has no Bluetooth. Neither matters here: the workload is one I²C read and one
network publish per interval, and ESP-NOW — the relevant fallback if WiFi
association ever proves too costly — is WiFi-based, not BLE.

### Two sensors per node, if wanted

The board exposes **two independent I²C buses**: one on the STEMMA QT connector,
one on the breakout pads. The VEML7700's address is fixed at 0x10 and cannot be
changed, but two of them on *separate buses* do not conflict. ESPHome supports
multiple `i2c:` buses with distinct IDs.

So a single node can carry a room-centre and a window sensor. The second sensor
requires soldering to the pads, so this is an option to reach for where it earns
its keep, not the default.

### Known traps

- **ESP32-S2 requires the esp-idf framework in ESPHome**, not Arduino. One
  config line; an evening lost without it.
- **Confirm the STEMMA QT bus GPIO numbers from Adafruit's pinout diagram.** The
  product description confirms the connector is a separate bus from the breakout
  pads but does not give pin numbers.
- The board's controllable power pin gates the **NeoPixel**, not the STEMMA QT
  bus — unlike the Feather ESP32-S2. No I²C power-enable step is needed. (Do
  disable the NeoPixel on any node later converted to battery.)

### Reference config

```yaml
esphome:
  name: south-window-lux
esp32:
  board: adafruit_qtpy_esp32s2
  framework: { type: esp-idf }

i2c: { sda: GPIO41, scl: GPIO40 }   # confirm against Adafruit pinout

sensor:
  - platform: veml7700
    address: 0x10
    update_interval: 15s
    ambient_light:
      name: "South Window Lux"
      filters:
        - median: { window_size: 5 }
```

### Battery power is deliberately deferred

The board has battery pads on the underside with diode protection (up to 6V in),
so converting a node later requires no rebuild. Deep sleep draws ~70µA.

Battery life is dominated not by sleep current but by **WiFi association on each
wake** — roughly 3s at ~120mA, so about 0.1mAh per reading. That makes sample
rate the only variable that matters:

| Interval | Consumption | 2000mAh LiPo |
|---|---|---|
| 15s (Phase 0 rate) | ~576 mAh/day | ~3.5 days |
| 5 min | ~30 mAh/day | ~65 days |

A 20× swing, decided entirely by a number Phase 0 exists to measure. Hence:
**mains power for the experiment, revisit with data.** If the required rate turns
out to be minutes rather than seconds, battery nodes become straightforward, and
ESP-NOW in place of WiFi would improve them further by cutting association cost.

Association time varies considerably with signal strength, so treat these as
order-of-magnitude figures. The conclusion — that 15s sampling rules out battery
and 5-minute sampling permits it — is not sensitive to the estimate.

### Placement

**Placement will affect results more than sensor choice.** A ceiling sensor
measures light reflected off the floor; a wall sensor at eye height measures
something closer to perception; a sensor that can see a lamp will fight it.
Expect to re-site sensors several times — the node design exists to make that
cheap.

Prices, model details and pinouts reflect knowledge current to January 2026 and
should be confirmed before purchase.

## Phasing

The ordering principle: all expensive learning happens before anything touches a
bulb. Phases 0–2 carry no risk to household harmony.

Each phase gets its own implementation plan. Phase 0's findings may invalidate
parts of later phases — that is the point — so planning them in detail now would
be waste.

**Phase 0 — Observe.** ~2 weeks, no control.
HA on the Pi with the Hue integration; the two existing sensors online. Assemble
the ESPHome nodes and site them: one south-facing window, one north-facing
window, one or two room-centre. luxctl is `ha_client` + `recorder` + tick loop
only; the dispatcher does not yet exist.

Node bring-up is two stages:

- **Bench** (~30 min). Plug one node together, flash it, confirm readings reach
  HA. Sanity-check magnitude in four conditions — dark room, lit room, by a
  window, outdoors in sun. Order of magnitude is what matters; a phone lux app is
  an adequate reference. Plausible across all four means the node works.
- **In situ.** Tape nodes where the sensors are wanted and leave them for several
  days. Deliberately unlovely — this is the placement experiment, and it is where
  a spot turns out to catch a reflection off a picture frame every afternoon.
  Enclosures come after placement settles, not before.

*Deliverable:* two weeks of lux at ~15s resolution in SQLite, and the analysis
that decides the project — correlating window sensors against room sensors. If
window sensors predict room brightness well, feedforward wins outright and the
feedback question is moot.

**Phase 1 — Calibrate.** One evening.
`calibrate.py` sweeps each light group through its brightness range after dark,
dwelling at each step, recording every sensor. Fit the contribution model,
inspect residuals.

*Deliverable:* a verdict on whether subtract-what-was-commanded is viable,
obtained in one evening rather than three weeks of guessing.

**Phase 2 — Shadow.** ~1 week, still no control.
Implement every strategy and the replay harness. Run all strategies in shadow
against live data, and replay them against Phase 0 recordings.

*Deliverable:* a side-by-side of what each strategy would have done across two
weeks of real weather. Obviously-broken strategies are caught before touching a
bulb.

**Phase 3 — Live, one room.** ~2 weeks.
A low-stakes room — an office, not the living room. Safety ladder fully wired;
kill switch tested before anything else. One strategy live, the rest in shadow,
override logging active.

*Deliverable:* a real override rate, and the practical failures that only appear
on contact with reality.

**Phase 4 — Expand and compare.** Ongoing.
More rooms, different strategies running simultaneously in different rooms,
assignments rotated. Metric: overrides/day against the `NullStrategy` baseline.

### A caveat on replay

Replay is exact for strategies whose input is exogenous — daylight sensors,
schedules. It is only approximate for closed-loop strategies, because a
counterfactual controller would have produced different bulb output and
therefore different sensor readings. Simulating that requires the calibration
model. Treat replayed closed-loop results as indicative, not authoritative.

## Evaluation

"Works best" needs a metric. Two signals, feeding one log:

- **Manual override as a negative vote.** Someone changing the lights shortly
  after an automated change is the system getting it wrong, recorded
  automatically, requiring no discipline from anyone. This is the best available
  signal and it is free.
- **A "this is wrong right now" button** — a virtual HomeKit switch that logs a
  timestamp. One afternoon of work, and it yields labelled failure cases to
  replay against.

Overrides per day per strategy, measured against `NullStrategy`, is crude but
genuinely comparative.

## Testing

- **Strategies** — unit tests over synthetic snapshots, with a golden scenario
  set: dawn, dusk, a passing cloud, someone switching on a floor lamp, a sensor
  going stale mid-afternoon.
- **Calibration** — fit against synthetic data with known coefficients, assert
  recovery within tolerance.
- **Safety ladder** — table-driven; every rule gets a test proving it blocks what
  it is meant to block.
- **Shell** — thin by construction; tested against a fake HA client, plus one
  integration test against a real HA instance.
- **Replay as integration test** — run every strategy over real recorded history
  and assert no crashes, no commands outside clamps, no absurd oscillation. A
  better test than anything hand-written.

## Open questions

These are deliberately deferred, each with a defined resolution point:

| Question | Resolved by |
|---|---|
| How many sensors, and where? | Phase 0 placement experiments |
| Are window sensors sufficient on their own? | Phase 0 correlation analysis |
| Is the calibration model viable? | Phase 1 residuals |
| Which room is the Phase 3 guinea pig? | Chosen at Phase 3; must be low-stakes |
| Which Govee models support LAN control? | Check the Govee Home app before Phase 2; cloud-only models are excluded from continuous dimming and restricted to threshold rules |

## Explicitly out of scope

- Colour temperature control. Brightness first; circadian colour is a separate
  project once the brightness loop is trusted.
- Any HomeKit-native implementation. Ruled out above.
- Rewriting existing Apple Home scenes or schedules. luxctl adds behaviour; it
  does not migrate what already works.

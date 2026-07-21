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
  ESP32+BH1750 ──ESPHome──┐
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
certified devices, so any cheaper sensor requires a second Zigbee coordinator.
Given that a hub is needed regardless, **ESP32 + BH1750 running ESPHome** is the
recommended addition — roughly $10 per room, mains-powered, continuous readings
at any chosen interval, and precise control over placement.

Avoid Aqara *motion* sensors for this purpose: they report illuminance bundled
with a motion event, so an empty room stops reporting light levels precisely
when the reading matters. The dedicated Aqara Light Sensor T1 does not have this
problem.

**Placement will affect results more than sensor cost.** A ceiling sensor
measures light reflected off the floor; a wall sensor at eye height measures
something closer to perception; a sensor that can see a lamp will fight it.
Expect to re-site sensors several times.

Prices and model details reflect knowledge current to January 2026 and should be
confirmed before purchase.

## Phasing

The ordering principle: all expensive learning happens before anything touches a
bulb. Phases 0–2 carry no risk to household harmony.

Each phase gets its own implementation plan. Phase 0's findings may invalidate
parts of later phases — that is the point — so planning them in detail now would
be waste.

**Phase 0 — Observe.** ~2 weeks, no control.
HA on the Pi with the Hue integration; the two existing sensors online. Build
ESP32+BH1750 nodes in ESPHome — build one and validate placement before
soldering the rest. Target one south-facing window, one north-facing window, and
one or two room-centre. luxctl is `ha_client` + `recorder` + tick loop only; the
dispatcher does not yet exist.

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

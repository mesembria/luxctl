"""Phase 0 deliverable: do window sensors predict room brightness?

If they do, feedforward control is sufficient and the closed-loop machinery
in the spec is unnecessary.
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from luxctl.shell.config import Config, load_config

RESAMPLE = "1min"

# Daylight is selected from the data itself rather than from sun position:
# a window sensor reading above this is seeing daylight. 50 lux sits well
# above a dark or artificially-lit room and well below even a heavily
# overcast window (~1000 lux), so it separates the two cleanly without
# needing to model sunrise and sunset.
DAYLIGHT_THRESHOLD_LUX = 50.0

# Below this many paired daylight minutes, report NaN instead of a
# correlation. A confident-looking r drawn from a handful of points is
# worse than an honest blank.
MIN_DAYLIGHT_SAMPLES = 60


def load_readings(db_path: Path, config: Config) -> pd.DataFrame:
    """Load all readings into a frame with one column per sensor.

    Resampled to one-minute means so sensors reporting at different times
    line up on a common index.
    """
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        raw = pd.read_sql_query(
            "SELECT sensor_id, lux, at FROM readings ORDER BY at",
            connection,
            parse_dates=["at"],
        )
    finally:
        connection.close()

    known = {sensor.id for sensor in config.sensors}
    raw = raw[raw["sensor_id"].isin(known)]

    wide = raw.pivot_table(index="at", columns="sensor_id", values="lux",
                           aggfunc="mean")
    wide = wide.resample(RESAMPLE).mean()

    # pivot_table only creates a column for sensor IDs actually present in
    # the data. A sensor configured in sensors.toml that never recorded a
    # single reading (dead from day one, or a typo'd entity_id) would then
    # have no column at all, and coverage()/correlations() would drop it
    # from every report without any indication it exists. Add an all-NaN
    # column for any such sensor so it shows up at 0% completeness instead
    # of vanishing silently. Column order: sensors that have data keep the
    # order pivot_table produced (deterministic — pivot_table sorts by
    # sensor_id); sensors with no data are appended in config order.
    for sensor in config.sensors:
        if sensor.id not in wide.columns:
            wide[sensor.id] = float("nan")

    wide.columns.name = None
    return wide


def coverage(frame: pd.DataFrame, expected_interval_s: int) -> pd.DataFrame:
    """Report how complete each sensor's series is.

    A correlation computed across a period where a sensor was dead is
    meaningless, so always read this before reading the correlations.
    """
    span = frame.index.max() - frame.index.min()
    expected = span.total_seconds() / expected_interval_s + 1

    # Names carry the 1-minute resampling deliberately: these are computed
    # on per-minute means, so the extremes are not the raw peaks a reader
    # would otherwise assume, and the count is minute-bins not readings.
    return pd.DataFrame({
        "minutes": frame.notna().sum(),
        "completeness": (frame.notna().sum() / expected).clip(upper=1.0),
        "min_lux_1min_mean": frame.min(),
        "max_lux_1min_mean": frame.max(),
    })


def _window_and_room_ids(frame: pd.DataFrame,
                         config: Config) -> tuple[list[str], list[str]]:
    windows = [s.id for s in config.sensors
               if s.placement == "window" and s.id in frame.columns]
    rooms = [s.id for s in config.sensors
             if s.placement == "room" and s.id in frame.columns]
    return windows, rooms


def correlations(frame: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Pearson r for every window sensor against every room sensor,
    across the whole record.

    This figure is CONFOUNDED and must never be read alone. Every lux
    sensor in a house reads near zero at night, so any two of them
    correlate strongly on the day/night envelope regardless of whether
    the window actually predicts the room. Use daylight_correlations()
    for the feedforward decision.
    """
    windows, rooms = _window_and_room_ids(frame, config)

    return pd.DataFrame(
        {room: {window: frame[window].corr(frame[room]) for window in windows}
         for room in rooms}
    )


def daylight_correlations(frame: pd.DataFrame, config: Config,
                          threshold: float = DAYLIGHT_THRESHOLD_LUX,
                          min_samples: int = MIN_DAYLIGHT_SAMPLES) -> pd.DataFrame:
    """Pearson r restricted to daylight hours — the figure to trust.

    Daylight is selected from the data itself: rows where the window
    sensor in question reads above `threshold`. That avoids needing sun
    position, and it is the right restriction because the question is
    whether the window predicts the room *when there is daylight to
    predict from*.

    A pair with fewer than `min_samples` daylight rows reports NaN rather
    than a confident-looking number computed from a handful of points.
    """
    windows, rooms = _window_and_room_ids(frame, config)

    result = {}
    for room in rooms:
        column = {}
        for window in windows:
            daylight = frame[frame[window] > threshold]
            paired = daylight[[window, room]].dropna()
            if len(paired) < min_samples:
                column[window] = float("nan")
            else:
                column[window] = paired[window].corr(paired[room])
        result[room] = column

    return pd.DataFrame(result)


def daylight_sample_counts(frame: pd.DataFrame, config: Config,
                           threshold: float = DAYLIGHT_THRESHOLD_LUX) -> pd.DataFrame:
    """How many paired daylight minutes each correlation rests on.

    Printed beside the daylight matrix so a NaN, or a correlation drawn
    from very few minutes, is immediately explicable.
    """
    windows, rooms = _window_and_room_ids(frame, config)

    return pd.DataFrame({
        room: {
            window: len(
                frame[frame[window] > threshold][[window, room]].dropna()
            )
            for window in windows
        }
        for room in rooms
    })


def artificial_light_hours(db_path: Path, config: Config) -> pd.DataFrame:
    """How long each configured light was on across the record.

    Light states are recorded precisely so room-sensor movements that were
    not caused by daylight can be explained. Without surfacing them, a room
    whose lamps were on for much of the run folds artificial light into its
    correlation invisibly.

    This is a summary, not a model: it flags which rooms deserve suspicion,
    it does not attempt to subtract the lights' contribution.
    """
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        raw = pd.read_sql_query(
            "SELECT light_id, is_on, at FROM light_states ORDER BY at",
            connection,
            parse_dates=["at"],
        )
    finally:
        connection.close()

    rows = {}
    for light in config.lights:
        states = raw[raw["light_id"] == light.id]
        if states.empty:
            rows[light.id] = {"hours_on": 0.0, "transitions": 0,
                              "recorded": False}
            continue

        # Each state persists until the next one, so the time a light spent
        # on is the sum of the gaps that begin with an is_on row.
        durations = states["at"].diff().shift(-1).dt.total_seconds()
        on_seconds = durations[states["is_on"] == 1].sum()

        rows[light.id] = {
            "hours_on": round(float(on_seconds) / 3600.0, 1),
            "transitions": int(states["is_on"].diff().abs().sum()),
            "recorded": True,
        }

    return pd.DataFrame(rows).T


def write_plots(frame: pd.DataFrame, config: Config, out_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)

    # A dark room legitimately reads 0 lux, and a plain log axis silently
    # drops non-positive points instead of raising, hiding genuinely dark
    # periods from the plots. symlog with a 1-lux linear threshold keeps
    # the log behaviour above 1 lux but still shows zero and near-zero
    # readings. correlations() is unaffected — it works on linear values.
    axis = frame.plot(figsize=(14, 6), linewidth=0.8)
    axis.set_yscale("symlog", linthresh=1)
    axis.set_ylabel("lux (symlog scale)")
    axis.set_title("Recorded illuminance")
    axis.figure.savefig(out_dir / "timeseries.png", dpi=120, bbox_inches="tight")
    plt.close(axis.figure)

    windows = [s.id for s in config.sensors
               if s.placement == "window" and s.id in frame.columns]
    rooms = [s.id for s in config.sensors
             if s.placement == "room" and s.id in frame.columns]

    for window in windows:
        for room in rooms:
            figure, axes = plt.subplots(figsize=(6, 6))
            axes.scatter(frame[window], frame[room], s=2, alpha=0.3)
            axes.set_xscale("symlog", linthresh=1)
            axes.set_yscale("symlog", linthresh=1)
            axes.set_xlabel(f"{window} (lux)")
            axes.set_ylabel(f"{room} (lux)")
            axes.set_title(f"r = {frame[window].corr(frame[room]):.3f}")
            figure.savefig(out_dir / f"scatter_{window}_vs_{room}.png",
                           dpi=120, bbox_inches="tight")
            plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0 correlation analysis")
    parser.add_argument("--db", type=Path, default=Path("data/luxctl.db"))
    parser.add_argument("--config", type=Path, default=Path("sensors.toml"))
    parser.add_argument("--out", type=Path, default=Path("analysis/out"))
    args = parser.parse_args()

    config = load_config(args.config)
    frame = load_readings(args.db, config)

    print(f"\nSpan: {frame.index.min()} to {frame.index.max()}")
    print(f"Samples: {len(frame)} minutes\n")

    print("Coverage — read this first; low completeness invalidates the rest")
    print(coverage(frame, expected_interval_s=60).to_string(), "\n")

    print(f"Artificial light — hours each light was on across the record.")
    print("A room whose lights were on for much of the run has correlations")
    print("contaminated by artificial light, not just daylight.")
    print(artificial_light_hours(args.db, config).to_string(), "\n")

    print("=" * 70)
    print("DAYLIGHT-ONLY Pearson r — THIS is the feedforward decision")
    print(f"(rows where the window sensor reads above "
          f"{DAYLIGHT_THRESHOLD_LUX:.0f} lux)")
    print("=" * 70)
    print(daylight_correlations(frame, config).to_string(), "\n")
    print("Paired daylight minutes behind each figure "
          f"(NaN above means fewer than {MIN_DAYLIGHT_SAMPLES}):")
    print(daylight_sample_counts(frame, config).to_string(), "\n")

    print("-" * 70)
    print("All-hours Pearson r — CONFOUNDED, shown for comparison only.")
    print("Every sensor reads ~0 at night, so any two correlate on the")
    print("day/night cycle alone. Do not base the decision on this.")
    print("-" * 70)
    print(correlations(frame, config).to_string(), "\n")

    write_plots(frame, config, args.out)
    print(f"Plots written to {args.out}")


if __name__ == "__main__":
    main()

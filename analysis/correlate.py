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
    wide.columns.name = None
    return wide


def coverage(frame: pd.DataFrame, expected_interval_s: int) -> pd.DataFrame:
    """Report how complete each sensor's series is.

    A correlation computed across a period where a sensor was dead is
    meaningless, so always read this before reading the correlations.
    """
    span = frame.index.max() - frame.index.min()
    expected = span.total_seconds() / expected_interval_s + 1

    return pd.DataFrame({
        "readings": frame.notna().sum(),
        "completeness": (frame.notna().sum() / expected).clip(upper=1.0),
        "min_lux": frame.min(),
        "max_lux": frame.max(),
    })


def correlations(frame: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Pearson r for every window sensor against every room sensor."""
    windows = [s.id for s in config.sensors
               if s.placement == "window" and s.id in frame.columns]
    rooms = [s.id for s in config.sensors
             if s.placement == "room" and s.id in frame.columns]

    return pd.DataFrame(
        {room: {window: frame[window].corr(frame[room]) for window in windows}
         for room in rooms}
    )


def write_plots(frame: pd.DataFrame, config: Config, out_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)

    axis = frame.plot(figsize=(14, 6), logy=True, linewidth=0.8)
    axis.set_ylabel("lux (log scale)")
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
            axes.set_xscale("log")
            axes.set_yscale("log")
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

    print("Pearson r, window sensors (rows) vs room sensors (columns)")
    print(correlations(frame, config).to_string(), "\n")

    write_plots(frame, config, args.out)
    print(f"Plots written to {args.out}")


if __name__ == "__main__":
    main()

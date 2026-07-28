"""
Grab whatever is currently in the DHO1204's memory and save it.

Usage:
    Set up + trigger + STOP the scope by hand, then:
        python grabTrace.py
    or from a notebook:
        from grabTrace import grab
        t, v, sr, fname = grab(channel=4)
"""

import os
import time
from datetime import datetime

import numpy as np
import pyvisa

# ---------- Configuration ----------
SCOPE_VISA = "USB0::6833::1552::HDO1B275M00060::0::INSTR"
SAVE_DIR = os.path.expanduser(
    "~/eedm_jml/daqAnalysisAndExperiments/MOT_fill/data"
)
CHANNEL = 4
CHUNK = int(1e5)
PREFIX = "motFill_70dBGain_50OhmOnPD_sideLock"


def fetch_waveform(scope, channel=CHANNEL):
    """Read full deep memory from a stopped scope. Returns (times, volts, sample_rate)."""
    scope.write(":STOP")
    time.sleep(0.1)

    scope.write(f":WAV:SOUR CHAN{channel}")
    scope.write(":WAV:MODE RAW")
    scope.write(":WAV:FORM BYTE")

    # format,type,points,count,xinc,xorig,xref,yinc,yorig,yref
    pre = scope.query(":WAV:PRE?").strip().split(",")
    n_pts = int(float(pre[2]))
    x_inc, x_orig = float(pre[4]), float(pre[5])
    y_inc, y_orig, y_ref = float(pre[7]), float(pre[8]), float(pre[9])

    try:
        n_pts = int(float(scope.query(":ACQ:MDEP?")))
    except ValueError:
        pass  # MDEP returned AUTO; keep preamble value

    raw = np.empty(n_pts, dtype=np.uint8)
    for start in range(0, n_pts, CHUNK):
        stop = min(start + CHUNK, n_pts)
        scope.write(f":WAV:STAR {start + 1}")
        scope.write(f":WAV:STOP {stop}")
        scope.query("*OPC?")
        raw[start:stop] = scope.query_binary_values(
            ":WAV:DATA?", datatype="B", container=np.array
        )
        print(f"  fetched {stop}/{n_pts} pts", end="\r")
    print()

    volts = (raw.astype(np.float32) - y_ref - y_orig) * y_inc
    times = x_orig + np.arange(n_pts, dtype=np.float64) * x_inc
    return times, volts, 1.0 / x_inc


def save_data(times, volts, sample_rate, prefix=PREFIX):
    os.makedirs(SAVE_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(SAVE_DIR, f"{prefix}_{timestamp}.npz")
    np.savez_compressed(
        fname,
        times=times,
        voltages=volts,
        sample_rate=sample_rate,
        timestamp=timestamp,
    )
    print(f"Saved {fname}  ({volts.nbytes / 1e6:.1f} MB raw)")
    return fname


def grab(channel=CHANNEL, prefix=PREFIX, visa=SCOPE_VISA):
    rm = pyvisa.ResourceManager()
    scope = rm.open_resource(visa)
    scope.chunk_size = 1024 * 1024
    scope.timeout = 30000
    try:
        print(f"Connected: {scope.query('*IDN?').strip()}")
        times, volts, sr = fetch_waveform(scope, channel)
        print(f"{len(volts)/1e6:.3f} Mpts @ {sr/1e6:.3f} MSa/s, "
              f"{(times[-1]-times[0])*1e3:.2f} ms span")
        fname = save_data(times, volts, sr, prefix)
        return times, volts, sr, fname
    finally:
        try:
            scope.close()
        except Exception:
            pass
        rm.close()


if __name__ == "__main__":
    times, voltages, sr, fname = grab()
import numpy as np
import matplotlib.pyplot as plt
import pyvisa
from datetime import datetime
import time
import os

# ---------- Configuration ----------
SCOPE_VISA = "USB0::6833::1552::HDO1B275M00060::0::INSTR"
SAVE_DIR = os.path.expanduser("~/eedm_jml/daqAnalysisAndExperiments/launch/measureTrackVelocityBeatNote/data_gitignore")

PD_CHANNEL = 4
PD_SCALE = 5e-3
PD_OFFSET = 10e-3
PD_COUPLING = "DC"

SAMPLE_RATE = 500e6           # not directly set; scope picks based on MDEP+TIMEBASE
RECORD_LENGTH = int(10e6)
TIMEBASE = 20e-3              # 20 ms/div -> 200 ms total window

TRIG_LEVEL = 1.5              # V on AUX
WAIT_TIMEOUT = 30            # seconds to wait for stage TTL

ACQ_MODE = "NORM"   # "NORM" for normal, "HRES" for high resolution


# ---------- Scope control ----------
def setup_scope(scope):
    scope.timeout = 30000
    scope.write("*RST")
    time.sleep(1.0)
    scope.write("*CLS")

    # Photodiode channel
    scope.write(f":CHAN{PD_CHANNEL}:DISP ON")
    scope.write(f":CHAN{PD_CHANNEL}:COUP {PD_COUPLING}")
    scope.write(f":CHAN{PD_CHANNEL}:SCAL {PD_SCALE}")
    scope.write(f":CHAN{PD_CHANNEL}:OFFS {PD_OFFSET}")

    for ch in [1, 2, 3]:
        if ch != PD_CHANNEL:
            scope.write(f":CHAN{ch}:DISP OFF")

    # Trigger on AUX input (stage controller TTL)
    scope.write(":TRIG:MODE EDGE")
    scope.write(":TRIG:EDGE:SOUR EXT")
    scope.write(":TRIG:EDGE:SLOP POS")
    scope.write(f":TRIG:EDGE:LEV {TRIG_LEVEL}")
    scope.write(":TRIG:SWE SING")

    # Timebase
    scope.write(f":TIM:SCAL {TIMEBASE}")
    scope.write(":TIM:OFFS 0.090")   # ~10 ms pre-trigger, rest post (adjust as needed)

    # Acquisition: deep memory, single shot
    scope.write(":ACQ:TYPE NORM")
    scope.write(f":ACQ:MDEP {RECORD_LENGTH}")
    # In setup_scope, replace the existing :ACQ:TYPE line:
    scope.write(f":ACQ:TYPE {ACQ_MODE}")

    md = float(scope.query(":ACQ:MDEP?"))
    print(f"Memory depth set: {md/1e6:.3f} Mpts")
    print(f"Timebase: {TIMEBASE*1e3:.1f} ms/div  ({TIMEBASE*10*1e3:.1f} ms total)")


def arm_scope(scope):
    """Arm for a single acquisition."""
    scope.write(":SING")
    time.sleep(0.2)


def prime_with_force_trigger(scope):
    """Force-trigger once to prime the scope, then re-arm."""
    print("Force-triggering to prime scope...")
    scope.write(":TFOR")
    time.sleep(0.5)
    print("Re-arming for real trigger...")
    arm_scope(scope)


def wait_for_stage_trigger(scope, timeout=WAIT_TIMEOUT):
    """Poll trigger status; block until acquisition completes (i.e. stage TTL fired and
    record finished)."""
    print(f"Waiting for stage TTL on AUX (timeout {timeout} s)...")
    t0 = time.time()
    last_state = None
    while time.time() - t0 < timeout:
        status = scope.query(":TRIG:STAT?").strip()
        if status != last_state:
            print(f"  trigger status: {status}")
            last_state = status
        if status == "STOP":
            print(f"  acquisition complete after {time.time()-t0:.2f} s")
            return True
        time.sleep(0.1)
    raise TimeoutError("No stage trigger / acquisition did not complete in time")


def fetch_waveform(scope):
    scope.write(f":WAV:SOUR CHAN{PD_CHANNEL}")
    scope.write(":WAV:MODE RAW")
    scope.write(":WAV:FORM BYTE")

    preamble = scope.query(":WAV:PRE?").strip().split(",")
    x_inc = float(preamble[4])
    x_orig = float(preamble[5])
    y_inc = float(preamble[7])
    y_orig = float(preamble[8])
    y_ref = float(preamble[9])

    total_pts = int(float(scope.query(":ACQ:MDEP?")))
    chunk = 1_000_000
    raw = np.empty(total_pts, dtype=np.uint8)

    for start in range(0, total_pts, chunk):
        stop = min(start + chunk, total_pts)
        scope.write(f":WAV:STAR {start+1}")
        scope.write(f":WAV:STOP {stop}")
        data = scope.query_binary_values(":WAV:DATA?", datatype="B", container=np.array)
        raw[start:stop] = data
        print(f"  fetched {stop}/{total_pts} pts", end="\r")
    print()

    voltages = (raw.astype(np.float32) - y_ref) * y_inc - y_orig
    times = x_orig + np.arange(total_pts) * x_inc
    return times, voltages


# ---------- Saving ----------
def save_data(times, voltages, sample_rate):
    os.makedirs(SAVE_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(SAVE_DIR, f"beat_{timestamp}.npz")
    np.savez_compressed(
        fname,
        times=times,
        voltages=voltages,
        sample_rate=sample_rate,
        timestamp=timestamp,
    )
    print(f"Saved {fname}  ({voltages.nbytes/1e6:.1f} MB raw)")
    return fname


# ---------- Plotting ----------
def plot_waveform(times, voltages):
    n = len(voltages)
    decim = max(1, n // 200_000)
    t_plot = times[::decim] * 1e3
    v_plot = voltages[::decim] * 1e3

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(t_plot, v_plot, lw=0.5)
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Beat signal (mV)")
    ax.set_title(f"Beat note, {n/1e6:.1f} Mpts")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    plt.show()


# ---------- Main ----------
def main():
    print("Connecting to scope...")
    rm = pyvisa.ResourceManager()
    scope = rm.open_resource(SCOPE_VISA)
    print(f"Connected: {scope.query('*IDN?').strip()}")

    try:
        setup_scope(scope)
        time.sleep(1.5)
        print("Start motion in Kinesis...")
        

        arm_scope(scope)
        prime_with_force_trigger(scope)

        wait_for_stage_trigger(scope, timeout=WAIT_TIMEOUT)

        sr = float(scope.query(":ACQ:SRAT?"))
        md = float(scope.query(":ACQ:MDEP?"))
        print(f"Actual sample rate: {sr/1e6:.3f} MSa/s,  memory depth: {md/1e6:.3f} Mpts")

        print("Fetching waveform...")
        times, voltages = fetch_waveform(scope)

        save_data(times, voltages, sr)
        plot_waveform(times, voltages)

    finally:
        scope.close()
        rm.close()


if __name__ == "__main__":
    main()
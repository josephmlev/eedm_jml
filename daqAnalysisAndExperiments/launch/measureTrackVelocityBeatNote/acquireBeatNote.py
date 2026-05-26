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
PD_SCALE = 5e-3            # V/div
PD_OFFSET = -10e-3          # V
PD_COUPLING = "DC"

RECORD_LENGTH = int(10e6)  # 10 Mpts
TIMEBASE = 20e-3#30e-3           # 20 ms/div -> 200 ms total window
TIME_OFFSET = 0.18#0.490        # trigger position (s); +ve = trigger left of center

TRIG_LEVEL = 1.5           # V on EXT/AUX
WAIT_TIMEOUT = 30          # seconds to wait for stage TTL

ACQ_MODE = "NORM"          # "NORM" or "HRES"


# ---------- Low-level helpers ----------
def wait_for_state(scope, target, timeout=5.0, poll=0.05):
    """Block until :TRIG:STAT? returns target (TD, WAIT, RUN, AUTO, STOP)."""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        s = scope.query(":TRIG:STAT?").strip()
        if s != last:
            last = s
        if s == target:
            return s
        time.sleep(poll)
    raise TimeoutError(f"Scope never reached state '{target}' (last='{last}')")


def opc(scope, timeout=10.0):
    """Block until pending operations complete."""
    old = scope.timeout
    scope.timeout = int(timeout * 1000)
    try:
        scope.query("*OPC?")
    finally:
        scope.timeout = old


# ---------- Scope control ----------
def setup_scope(scope):
    scope.timeout = 30000

    scope.write("*CLS")
    scope.write("*RST")
    opc(scope, timeout=15.0)
    time.sleep(0.5)  # extra settling after reset
    scope.write("*CLS")

    # Stop while configuring
    scope.write(":STOP")

    # Photodiode channel
    scope.write(f":CHAN{PD_CHANNEL}:DISP ON")
    scope.write(f":CHAN{PD_CHANNEL}:COUP {PD_COUPLING}")
    scope.write(f":CHAN{PD_CHANNEL}:SCAL {PD_SCALE}")
    scope.write(f":CHAN{PD_CHANNEL}:OFFS {PD_OFFSET}")
    scope.write(f":CHAN{PD_CHANNEL}:BWL OFF")

    # Disable other channels
    for ch in (1, 2, 3, 4):
        if ch != PD_CHANNEL:
            scope.write(f":CHAN{ch}:DISP OFF")

    # Timebase first (memory depth depends on it)
    scope.write(f":TIM:SCAL {TIMEBASE}")
    scope.write(f":TIM:OFFS {TIME_OFFSET}")

    # Acquisition: deep memory
    scope.write(f":ACQ:TYPE {ACQ_MODE}")
    scope.write(f":ACQ:MDEP {RECORD_LENGTH}")

    # Trigger on EXT/AUX (stage controller TTL)
    scope.write(":TRIG:MODE EDGE")
    scope.write(":TRIG:EDGE:SOUR EXT")
    scope.write(":TRIG:EDGE:SLOP POS")
    scope.write(f":TRIG:EDGE:LEV {TRIG_LEVEL}")
    scope.write(":TRIG:SWE SING")
    scope.write(":TRIG:COUP DC")

    opc(scope, timeout=10.0)

    md = float(scope.query(":ACQ:MDEP?"))
    sr = float(scope.query(":ACQ:SRAT?"))
    print(f"  Memory depth: {md/1e6:.3f} Mpts")
    print(f"  Sample rate:  {sr/1e6:.3f} MSa/s")
    print(f"  Timebase:     {TIMEBASE*1e3:.1f} ms/div ({TIMEBASE*10*1e3:.1f} ms total)")
    print(f"  Trigger:      EXT, edge POS, level {TRIG_LEVEL} V, single sweep")


def arm_single(scope):
    """Issue :SING and wait until the scope reports WAIT (i.e. armed)."""
    scope.write(":SING")
    wait_for_state(scope, "WAIT", timeout=5.0)


def prime_trigger(scope):
    """
    Workaround for the DHO1000 'first trigger after settings change is missed'
    quirk: arm, force-trigger to flush, then re-arm cleanly.
    """
    print("Priming scope (force trigger to flush first-trigger quirk)...")
    arm_single(scope)
    scope.write(":TFOR")
    wait_for_state(scope, "STOP", timeout=10.0)
    arm_single(scope)
    print("Scope armed and waiting for real trigger.")


def wait_for_trigger(scope, timeout=WAIT_TIMEOUT):
    """Poll trigger status; return when acquisition completes (STOP)."""
    print(f"Waiting for stage TTL on EXT (timeout {timeout} s)...")
    t0 = time.time()
    last_state = None
    while time.time() - t0 < timeout:
        status = scope.query(":TRIG:STAT?").strip()
        if status != last_state:
            print(f"  trigger status: {status}  (t={time.time()-t0:.2f}s)")
            last_state = status
        if status == "STOP":
            print(f"  acquisition complete after {time.time()-t0:.2f} s")
            return True
        time.sleep(0.05)
    raise TimeoutError("No stage trigger / acquisition did not complete in time")


def fetch_waveform(scope):
    # Scope MUST be stopped to read deep memory in RAW mode
    scope.write(":STOP")
    time.sleep(0.1)
    wait_for_state(scope, "STOP", timeout=2.0)

    scope.write(f":WAV:SOUR CHAN{PD_CHANNEL}")
    scope.write(":WAV:MODE RAW")
    scope.write(":WAV:FORM BYTE")

    preamble = scope.query(":WAV:PRE?").strip().split(",")
    # Rigol preamble: format,type,points,count,xinc,xorig,xref,yinc,yorig,yref
    x_inc = float(preamble[4])
    x_orig = float(preamble[5])
    y_inc = float(preamble[7])
    y_orig = float(preamble[8])   # in raw counts (level), not volts
    y_ref = float(preamble[9])    # in raw counts

    total_pts = int(float(scope.query(":ACQ:MDEP?")))
    chunk = int(1e5)
    raw = np.empty(total_pts, dtype=np.uint8)

    def read_chunk(start, stop, max_retries=3):
        for attempt in range(max_retries):
            try:
                scope.write(f":WAV:STAR {start+1}")
                scope.write(f":WAV:STOP {stop}")
                scope.query("*OPC?")
                return scope.query_binary_values(
                    ":WAV:DATA?", datatype="B", container=np.array
                )
            except pyvisa.errors.VisaIOError as e:
                print(f"\n  chunk {start}-{stop} attempt {attempt+1} failed: {e}")
                try:
                    scope.clear()
                except Exception as ce:
                    print(f"  clear() failed: {ce}")
                time.sleep(0.5)
        raise RuntimeError(f"Chunk {start}-{stop} failed after {max_retries} retries")

    for start in range(0, total_pts, chunk):
        stop = min(start + chunk, total_pts)
        raw[start:stop] = read_chunk(start, stop)
        print(f"  fetched {stop}/{total_pts} pts", end="\r")
    print()

    # Correct Rigol conversion: V = (raw - yref - yorig) * yinc
    voltages = (raw.astype(np.float32) - y_ref - y_orig) * y_inc
    times = x_orig + np.arange(total_pts, dtype=np.float64) * x_inc
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

def plot_inst_velocity(
    times,
    voltages,
    sr,
    v_center_mm,           # center velocity to filter around [mm/s]
    v_halfwidth_mm,        # +/- half-width for bandpass and zoom [mm/s]
    t_start=None,          # seconds; None = use full record
    t_stop=None,
    wavelength=852.34727582e-9,
    n_bounces=16,
    seg_dur=1e-3,          # waterfall segment length [s]
    overlap=0.0,           # waterfall overlap fraction 0..1
    filt_order=4,
    smooth_ms=0.5,         # Hilbert f_inst smoothing window [ms]
    window_name="hann",
    cmap="viridis",
    db_range=60,           # color scale dynamic range below max [dB]
    title=None,
    x0=None,           # mm, position at t=0           
    accel=None,            # mm/s^2, constant acceleration
    pos_unit="mm",
    ):
    """
    Plot a STFT waterfall (time vs velocity) with the Hilbert instantaneous
    velocity overlaid in red. Bandpass and y-axis zoom are derived from
    v_center_mm +/- v_halfwidth_mm.
    """
    from scipy.signal import hilbert, butter, filtfilt, windows
    from scipy.ndimage import uniform_filter1d

    fs = float(sr)
    xinc = 1.0 / fs
    vf = v_center_mm

    # ---- velocity <-> frequency helpers ----
    def f_to_v(f_hz):  return wavelength * f_hz / n_bounces        # m/s
    def v_to_f(v_ms):  return n_bounces * v_ms / wavelength        # Hz

    # ---- band edges from center +/- halfwidth (mm/s -> Hz) ----
    v_lo_mm = v_center_mm - v_halfwidth_mm
    v_hi_mm = v_center_mm + v_halfwidth_mm
    if v_lo_mm <= 0:
        raise ValueError("v_center - v_halfwidth must be > 0 mm/s")
    f_lo = v_to_f(v_lo_mm * 1e-3)
    f_hi = v_to_f(v_hi_mm * 1e-3)
    nyq = fs / 2
    if f_hi >= nyq:
        raise ValueError(f"Upper band ({f_hi/1e6:.3f} MHz) exceeds Nyquist "
                        f"({nyq/1e6:.3f} MHz). Reduce v_center or halfwidth.")

    # ---- time-window crop ----
    mask = np.ones_like(times, dtype=bool)
    if t_start is not None: mask &= times >= t_start
    if t_stop  is not None: mask &= times <= t_stop
    t_cut = times[mask]
    v_cut = voltages[mask]
    if len(v_cut) < 10:
        raise ValueError("Time window too short / empty after cropping.")

    # ---- bandpass ----
    b, a = butter(filt_order, [f_lo/nyq, f_hi/nyq], btype="band")
    v_filt = filtfilt(b, a, v_cut)

    # ---- Hilbert instantaneous frequency -> velocity ----
    analytic = hilbert(v_filt)
    phase = np.unwrap(np.angle(analytic))
    f_inst = np.gradient(phase, xinc) / (2 * np.pi)

    nsmooth = max(1, int(round(smooth_ms * 1e-3 * fs)))
    if nsmooth > 1:
        f_inst_s = uniform_filter1d(f_inst, size=nsmooth, mode="nearest")
    else:
        f_inst_s = f_inst
    v_inst_mm = f_to_v(f_inst_s) * 1e3

    # ---- STFT waterfall ----
    nperseg = int(round(seg_dur / xinc))
    nstep   = max(1, int(round(nperseg * (1 - overlap))))
    nseg    = 1 + (len(v_cut) - nperseg) // nstep
    if nseg < 1:
        raise ValueError(f"Cut region ({len(v_cut)*xinc*1e3:.2f} ms) shorter "
                        f"than segment ({seg_dur*1e3:.2f} ms)")

    win = windows.get_window(window_name, nperseg)
    win_norm = np.sum(win**2)

    freqs_wf  = np.fft.rfftfreq(nperseg, d=xinc)
    seg_times = t_cut[0] + (np.arange(nseg) * nstep + nperseg / 2) * xinc

    spec = np.empty((nseg, len(freqs_wf)), dtype=np.float32)
    for i in range(nseg):
        chunk = v_cut[i*nstep : i*nstep + nperseg]
        chunk = (chunk - chunk.mean()) * win
        X = np.fft.rfft(chunk)
        psd = (np.abs(X)**2) / (fs * win_norm)
        psd[1:-1] *= 2
        spec[i] = psd
    spec_db = 10 * np.log10(spec + 1e-20)

    # ---- decimation for overlay ----
    DECIM = max(1, len(t_cut) // 100000)

    # ---- plot ----
    vels_mm_axis = f_to_v(freqs_wf) * 1e3

    fig, ax = plt.subplots(figsize=(11, 7))
    im = ax.pcolormesh(
        seg_times * 1e3,
        vels_mm_axis,
        spec_db.T,
        shading="auto",
        cmap=cmap,
    )
    im.set_clim(spec_db.max() - db_range, spec_db.max())

    ax.plot(
        t_cut[::DECIM] * 1e3,
        v_inst_mm[::DECIM],
        color="red", lw=0.8, alpha=0.85,
        label="Hilbert inst. velocity",
    )
    ax.axhline(v_center_mm, color="white", lw=0.6, ls="--", alpha=0.5)

    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Track speed (mm/s)")
    ax.set_title(title or f"Waterfall + Hilbert  "
                        f"(center {v_center_mm:g} ± {v_halfwidth_mm:g} mm/s)")
    ax.set_ylim(v_lo_mm, v_hi_mm)

    # ---- fractional error reference lines around v_center ----
    for pct, ls in [(0.1, ":"), (0.25, "--"), (0.5, "-.")]:
        dv = v_center_mm * pct / 100.0
        ax.axhline(v_center_mm + dv, color="white", lw=0.7, ls=ls, alpha=0.6,
                   label=f"±{pct}%")
        ax.axhline(v_center_mm - dv, color="white", lw=0.7, ls=ls, alpha=0.6)


    ax.legend(loc="upper right")

    secax = ax.secondary_yaxis(
        "right",
        functions=(
            lambda v_mm:  v_to_f(v_mm * 1e-3) / 1e6,
            lambda f_mhz: f_to_v(f_mhz * 1e6) * 1e3,
        ),
    )
    secax.set_ylabel("Frequency (MHz)", labelpad=8)

    fig.colorbar(im, ax=ax, label="PSD (dB)", pad=0.12)
    if x0 is not None and vf is not None and accel is not None:
        x0_f    = float(x0)
        vf_f    = float(vf)
        accel_f = float(accel)
        t_ramp  = vf_f / accel_f if accel_f > 0 else 0.0   # s, accel phase duration
        x_ramp  = x0_f + 0.5 * accel_f * t_ramp**2         # mm, position at end of ramp

        def t_ms_to_x(t_ms, x0=x0_f, vf=vf_f, a=accel_f,
                      t_r=t_ramp, x_r=x_ramp):
            t = np.asarray(t_ms, dtype=float) * 1e-3
            x = np.where(
                t <= t_r,
                x0 + 0.5 * a * t**2,                       # accelerating
                x_r + vf * (t - t_r),                      # coasting
            )
            return x

        def x_to_t_ms(x, x0=x0_f, vf=vf_f, a=accel_f,
                      t_r=t_ramp, x_r=x_ramp):
            x = np.asarray(x, dtype=float)
            t = np.where(
                x <= x_r,
                np.sqrt(np.maximum(2 * (x - x0) / a, 0)) if a > 0 else (x - x0)/vf,
                t_r + (x - x_r) / vf,
            )
            return t * 1e3

        topax = ax.secondary_xaxis("top", functions=(t_ms_to_x, x_to_t_ms))
        topax.set_xlabel(f"Track position ({pos_unit})")
    fig.tight_layout()
    plt.show()

    return {
        "t_cut": t_cut,
        "v_inst_mm": v_inst_mm,
        "seg_times": seg_times,
        "freqs_wf": freqs_wf,
        "spec_db": spec_db,
        "f_lo": f_lo,
        "f_hi": f_hi,
    }


# ---------- Main ----------
def main():
    print("Connecting to scope...")
    rm = pyvisa.ResourceManager()
    scope = rm.open_resource(SCOPE_VISA)
    scope.chunk_size = 1024 * 1024     # 1 MB; well above your 100 kpt chunks
    scope.timeout = 30000
    print(f"Connected: {scope.query('*IDN?').strip()}")

    try:
        print("Configuring scope...")
        setup_scope(scope)

        print("Start motion in Kinesis when ready.")
        prime_trigger(scope)

        wait_for_trigger(scope, timeout=WAIT_TIMEOUT)

        sr = float(scope.query(":ACQ:SRAT?"))
        md = float(scope.query(":ACQ:MDEP?"))
        print(f"Actual sample rate: {sr/1e6:.3f} MSa/s,  memory depth: {md/1e6:.3f} Mpts")

        print("Fetching waveform...")
        times, voltages = fetch_waveform(scope)

        save_data(times, voltages, sr)
        #plot_waveform(times, voltages)
        plot_inst_velocity(
            times, voltages, sr,
            v_center_mm=300, v_halfwidth_mm=2.0,
            t_start=0.05, t_stop=0.30,
            x0=100, accel=3000,    # constant velocity sweep
        )
        
        return times, voltages, sr

    finally:
        try:
            scope.close()
        except Exception:
            pass
        rm.close()



if __name__ == "__main__":
    times, voltages, sr = main()
import matplotlib.pyplot as plt
import numpy as np


def trapezoidal_move(distance_mm, v_max_mm_s, accel_mm_s2,
                     start_pos=None, plot=True):
    """Analyze a trapezoidal velocity profile move.
    
    Args:
        distance_mm: total move distance in mm
        v_max_mm_s: maximum velocity in mm/s
        accel_mm_s2: acceleration (and deceleration) in mm/s^2
        start_pos: starting position in mm (default: set so pos=150 at v_max)
        plot: whether to show position and velocity plots
    """
    distance = abs(distance_mm)
    v_max = abs(v_max_mm_s)
    a = abs(accel_mm_s2)

    # Time to accelerate to v_max
    t_accel = v_max / a

    # Distance covered during accel and decel
    d_accel = 0.5 * a * t_accel**2
    d_decel = d_accel
    d_ramp = d_accel + d_decel

    if d_ramp > distance:
        t_accel = (distance / a) ** 0.5
        v_peak = a * t_accel
        t_total = 2 * t_accel
        print(f"WARNING: Never reaches v_max!")
        print(f"  Peak velocity: {v_peak:.4f} mm/s")
        print(f"  Time to peak:  {t_accel*1e3:.4f} ms")
        print(f"  Total time:    {t_total*1e3:.4f} ms")
        print(f"  Time at v_max: 0 ms")
        if start_pos is None:
            start_pos = 150.0 - 0.5 * distance
        print(f"\n  Start position: {start_pos:.4f} mm")
        return

    # Constant velocity phase
    d_cruise = distance - d_ramp
    t_cruise = d_cruise / v_max
    t_decel = t_accel
    t_total = t_accel + t_cruise + t_decel

    t_hit_vmax = t_accel
    t_leave_vmax = t_accel + t_cruise
    d_at_vmax = d_accel

    start_pos_theoretical = 150.0 - d_at_vmax
    if start_pos is None:
        start_pos = start_pos_theoretical

    print(f"=== Trapezoidal Move Profile ===")
    print(f"  Distance:      {distance:.4f} mm")
    print(f"  V_max:         {v_max:.4f} mm/s")
    print(f"  Acceleration:  {a:.4f} mm/s^2")
    print()
    print(f"  Time to v_max:   {t_accel*1e3:.4f} ms")
    print(f"  Hit v_max at:    {t_hit_vmax*1e3:.4f} ms")
    print(f"  Leave v_max at:  {t_leave_vmax*1e3:.4f} ms")
    print(f"  Time at v_max:   {t_cruise*1e3:.4f} ms")
    print(f"  Decel time:      {t_decel*1e3:.4f} ms")
    print(f"  Total time:      {t_total*1e3:.4f} ms")
    print()
    print(f"  Dist during accel: {d_accel:.4f} mm")
    print(f"  Dist during cruise: {d_cruise:.4f} mm")
    print(f"  Dist during decel: {d_decel:.4f} mm")
    print()
    print(f"  Start position (pos=150 at v_max): "
          f"{start_pos_theoretical:.4f} mm")

    if plot:
        # Key time points
        times = np.array([0, t_accel, t_accel + t_cruise, t_total])
        velocities = np.array([0, v_max, v_max, 0])
        positions = np.array([
            start_pos,
            start_pos + d_accel,
            start_pos + d_accel + d_cruise,
            start_pos + distance,
        ])

        # Dense arrays for smooth curves
        t_dense = np.linspace(0, t_total, 1000)
        v_dense = np.piecewise(
            t_dense,
            [t_dense <= t_accel,
             (t_dense > t_accel) & (t_dense <= t_accel + t_cruise),
             t_dense > t_accel + t_cruise],
            [lambda t: a * t,
             lambda t: v_max,
             lambda t: v_max - a * (t - t_accel - t_cruise)]
        )
        x_dense = np.piecewise(
            t_dense,
            [t_dense <= t_accel,
             (t_dense > t_accel) & (t_dense <= t_accel + t_cruise),
             t_dense > t_accel + t_cruise],
            [lambda t: start_pos + 0.5 * a * t**2,
             lambda t: start_pos + d_accel + v_max * (t - t_accel),
             lambda t: (start_pos + d_accel + d_cruise
                        + v_max * (t - t_accel - t_cruise)
                        - 0.5 * a * (t - t_accel - t_cruise)**2)]
        )

        fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))

        # Velocity plot
        ax1.plot(t_dense * 1e3, v_dense, 'b-', linewidth=2)
        ax1.axhline(y=v_max, color='b', linestyle='--', alpha=0.3)
        ax1.set_ylabel('Velocity (mm/s)')
        ax1.set_title('Trapezoidal Move Profile')
        ax1.grid(True, alpha=0.3)

        # Position plot
        ax2.plot(t_dense * 1e3, x_dense, 'r-', linewidth=2)
        ax2.axhline(y=150, color='k', linestyle='--', alpha=0.3,
                     label='150 mm')
        ax2.set_xlabel('Time (ms)')
        ax2.set_ylabel('Position (mm)')
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.show()


if __name__ == "__main__":
    trapezoidal_move(
        distance_mm=30,
        v_max_mm_s=15,
        accel_mm_s2=5000
    )
    
    
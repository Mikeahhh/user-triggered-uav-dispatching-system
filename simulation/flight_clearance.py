from __future__ import annotations

import numpy as np


def flight_clearance(east, north, altitude, ground_at, minimum=2.0, step=5.0):
    axes = [np.asarray(value, dtype=float).reshape(-1) for value in (east, north, altitude)]
    if not axes[0].size or len({value.size for value in axes}) != 1:
        raise ValueError("flight coordinates must have equal nonzero length")
    positions = np.column_stack(axes)
    if (not np.all(np.isfinite(positions)) or not np.isfinite(minimum)
            or minimum <= 0 or not np.isfinite(step) or step <= 0):
        raise ValueError("flight coordinates and clearance limits must be finite and valid")
    pieces = [positions[:1]]
    for start, finish in zip(positions[:-1], positions[1:]):
        count = max(1, int(np.ceil(np.linalg.norm(finish[:2] - start[:2]) / step)))
        fraction = np.arange(1, count + 1, dtype=float)[:, None] / count
        pieces.append(start + fraction * (finish - start))
    samples = np.concatenate(pieces)
    ground = np.asarray(ground_at(samples[:, 0], samples[:, 1]), dtype=float).reshape(-1)
    if ground.size != len(samples):
        raise ValueError("terrain samples must match flight sample count")
    clearance = samples[:, 2] - ground
    finite = bool(np.all(np.isfinite(ground)))
    spacing = np.linalg.norm(np.diff(samples[:, :2], axis=0), axis=1)
    return {
        "passed": finite and bool(np.all(clearance >= minimum - 1e-9)),
        "minimum_clearance_m": float(clearance.min()) if finite else None,
        "required_clearance_m": float(minimum),
        "sample_count": len(samples),
        "maximum_sample_spacing_m": float(spacing.max(initial=0)),
    }

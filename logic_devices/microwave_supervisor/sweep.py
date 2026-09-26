"""Validation and point generation for the software discrete sweep."""

import math


def sweep_points(start, stop, step, *, maximum_points=100000):
    start, stop, step = map(float, (start, stop, step))
    if not all(map(math.isfinite, (start, stop, step))):
        raise ValueError("sweep values must be finite")
    if step == 0:
        raise ValueError("sweep step cannot be zero")
    if start < stop and step < 0:
        raise ValueError("ascending sweep requires a positive step")
    if start > stop and step > 0:
        raise ValueError("descending sweep requires a negative step")
    if start == stop:
        return [start]
    span = stop - start
    count = int(math.floor(abs(span / step) + 1e-12)) + 1
    if count > maximum_points:
        raise ValueError(f"sweep exceeds {maximum_points} points")
    points = [start + index * step for index in range(count)]
    # Always execute the requested endpoint once, even when the step does not
    # divide the span exactly. Avoid a duplicate caused by floating point noise.
    if not math.isclose(points[-1], stop, rel_tol=0, abs_tol=1e-12):
        points.append(stop)
    else:
        points[-1] = stop
    return points


def validate_dwell(seconds):
    value = float(seconds)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("sweep dwell time must be greater than zero")
    return value

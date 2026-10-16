"""Lesson metrics, as defined in section 5 of the spec."""
from __future__ import annotations

from typing import Callable, Dict, List, Sequence, Tuple

from .lesson_file import Lesson

MIN_EVENT_SAMPLES = 15    # 0.3 s at 50 Hz
MERGE_GAP_SAMPLES = 50    # runs closer than 1 s at 50 Hz are one event
OVER_LIMIT_MARGIN_KMH = 5.0
MOVING_THRESHOLD_KMH = 2.0
G = 9.81

# Stored metrics are rounded so that re-evaluating the stored values gives
# exactly the same result as the original evaluation (audit reproducibility).
PRECISION = 4

METRIC_NAMES = (
    "duration_min", "distance_km", "avg_moving_speed_kmh", "pct_time_over_limit",
    "harsh_brake_count", "harsh_accel_count", "sharp_turn_count", "max_lateral_g",
    "lesson_score",
)

EVENT_TYPES: Dict[str, Tuple[str, Callable[[Lesson, int], bool], Callable[[Lesson, int], float]]] = {
    # metric name: (event type, condition per sample, severity value per sample)
    "harsh_brake_count": ("HARSH_BRAKE", lambda l, i: l.accel_long[i] <= -3.5, lambda l, i: l.accel_long[i]),
    "harsh_accel_count": ("HARSH_ACCEL", lambda l, i: l.accel_long[i] >= 3.0, lambda l, i: l.accel_long[i]),
    "sharp_turn_count": ("SHARP_TURN", lambda l, i: abs(l.accel_lat[i]) >= 4.0, lambda l, i: abs(l.accel_lat[i])),
}


def find_runs(flags: Sequence[bool]) -> List[Tuple[int, int]]:
    """Return [start, end) index pairs of consecutive True values."""
    runs = []
    start = None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(flags)))
    return runs


def find_events(flags: Sequence[bool], min_samples: int = MIN_EVENT_SAMPLES,
                merge_gap: int = MERGE_GAP_SAMPLES) -> List[Tuple[int, int]]:
    """Events: runs of >= min_samples samples; qualifying runs separated by
    fewer than merge_gap samples are merged into one event.

    Runs shorter than min_samples are sensor noise and are ignored entirely
    (they neither count nor bridge two events).
    """
    events: List[Tuple[int, int]] = []
    for start, end in find_runs(flags):
        if end - start < min_samples:
            continue
        if events and start - events[-1][1] < merge_gap:
            events[-1] = (events[-1][0], end)
        else:
            events.append((start, end))
    return events


def compute(lesson: Lesson) -> Tuple[Dict[str, float], List[dict]]:
    """Return (metrics, events). Events carry a timeline for instructors."""
    n = lesson.n_samples
    hz = lesson.sample_rate_hz
    speed = lesson.speed_kmh

    duration_min = n / hz / 60
    distance_km = sum(s / 3.6 / hz for s in speed) / 1000
    moving = [s for s in speed if s > MOVING_THRESHOLD_KMH]
    avg_moving = sum(moving) / len(moving) if moving else 0.0
    over = sum(1 for s, lim in zip(speed, lesson.speed_limit_kmh) if s > lim + OVER_LIMIT_MARGIN_KMH)
    pct_over = 100.0 * over / n
    max_lat_g = max(abs(a) for a in lesson.accel_lat) / G

    counts: Dict[str, int] = {}
    timeline: List[dict] = []
    for metric, (event_type, condition, severity) in EVENT_TYPES.items():
        flags = [condition(lesson, i) for i in range(n)]
        events = find_events(flags)
        counts[metric] = len(events)
        for start, end in events:
            worst = max(range(start, end), key=lambda i: abs(severity(lesson, i)))
            timeline.append({
                "type": event_type,
                "at_s": round(lesson.t_ms[start] / 1000, 2),
                "duration_s": round((end - start) / hz, 2),
                "peak": round(severity(lesson, worst), 3),
                "speed_kmh": round(speed[start], 1),
            })
    timeline.sort(key=lambda e: e["at_s"])

    metrics = {
        "duration_min": duration_min,
        "distance_km": distance_km,
        "avg_moving_speed_kmh": avg_moving,
        "pct_time_over_limit": pct_over,
        "harsh_brake_count": counts["harsh_brake_count"],
        "harsh_accel_count": counts["harsh_accel_count"],
        "sharp_turn_count": counts["sharp_turn_count"],
        "max_lateral_g": max_lat_g,
    }
    metrics = {k: round(v, PRECISION) if isinstance(v, float) else v for k, v in metrics.items()}
    score = (100
             - 2 * metrics["pct_time_over_limit"]
             - 5 * metrics["harsh_brake_count"]
             - 3 * metrics["harsh_accel_count"]
             - 4 * metrics["sharp_turn_count"])
    metrics["lesson_score"] = round(min(100.0, max(0.0, score)), PRECISION)
    return metrics, timeline

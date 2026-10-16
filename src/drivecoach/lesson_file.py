"""Parse and validate a lesson file uploaded by an in-car box.

A file is a block of `# key: value` header lines followed by a CSV table.
Validation errors are permanent (re-processing the same bytes will fail the
same way), so callers quarantine the file instead of retrying.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

REQUIRED_HEADERS = (
    "lesson_id", "student_id", "instructor_id", "car_id", "lesson_type",
    "started_at", "sample_rate_hz", "speed_unit",
)
EXPECTED_COLUMNS = [
    "t_ms", "speed", "accel_long_ms2", "accel_lat_ms2", "steering_deg", "speed_limit_kmh",
]
LESSON_TYPES = ("REGULAR", "PRACTICE")

# Factor to convert each supported speed unit to km/h.
SPEED_UNITS = {"kmh": 1.0, "mph": 1.609344, "ms": 3.6}
# Spellings accepted in the header, normalised before fingerprinting.
SPEED_UNIT_ALIASES = {"km/h": "kmh", "kph": "kmh", "m/s": "ms"}

# Identifiers end up in DynamoDB keys and S3 paths, so keep them boring.
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Physically implausible values mean a broken sensor, not a bad driver.
MAX_SPEED_KMH = 300.0
MAX_ABS_ACCEL_MS2 = 30.0


class LessonFileError(ValueError):
    """The file can never be processed as-is. `code` is a stable, PII-free reason."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class Lesson:
    header: Dict[str, str]
    t_ms: List[float] = field(repr=False)
    speed_kmh: List[float] = field(repr=False)
    accel_long: List[float] = field(repr=False)
    accel_lat: List[float] = field(repr=False)
    steering_deg: List[float] = field(repr=False)
    speed_limit_kmh: List[float] = field(repr=False)
    fingerprint: str = ""

    @property
    def lesson_id(self) -> str:
        return self.header["lesson_id"]

    @property
    def student_id(self) -> str:
        return self.header["student_id"]

    @property
    def lesson_type(self) -> str:
        return self.header["lesson_type"]

    @property
    def started_at(self) -> str:
        return self.header["started_at"]

    @property
    def sample_rate_hz(self) -> float:
        return float(self.header["sample_rate_hz"])

    @property
    def n_samples(self) -> int:
        return len(self.t_ms)


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp such as 2026-10-05T08:00:00Z."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def normalize_timestamp(value: str) -> str:
    return parse_timestamp(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(data: bytes) -> Lesson:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LessonFileError("NOT_UTF8")

    lines = text.splitlines()
    header: Dict[str, str] = {}
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        line = lines[i]
        i += 1
        if not line.strip():
            continue
        key, sep, value = line[1:].partition(":")
        if not sep:
            raise LessonFileError("BAD_HEADER_LINE", f"line {i}")
        header[key.strip()] = value.strip()

    _validate_header(header)

    if i >= len(lines):
        raise LessonFileError("NO_TABLE")
    columns = [c.strip() for c in lines[i].split(",")]
    if columns != EXPECTED_COLUMNS:
        raise LessonFileError("BAD_COLUMNS", ",".join(columns)[:200])
    i += 1

    factor = SPEED_UNITS[header["speed_unit"]]
    t_ms, speed, a_long, a_lat, steer, limit = [], [], [], [], [], []
    body = hashlib.sha256()
    for line_no in range(i, len(lines)):
        line = lines[line_no]
        if not line.strip():
            continue
        body.update(line.encode())
        body.update(b"\n")
        parts = line.split(",")
        if len(parts) != len(EXPECTED_COLUMNS):
            raise LessonFileError("BAD_ROW", f"line {line_no + 1}: {len(parts)} fields")
        try:
            values = [float(p) for p in parts]
        except ValueError:
            raise LessonFileError("BAD_ROW", f"line {line_no + 1}: not numeric")
        if any(v != v or v in (float("inf"), float("-inf")) for v in values):
            raise LessonFileError("BAD_ROW", f"line {line_no + 1}: NaN/inf")
        t_ms.append(values[0])
        speed.append(values[1] * factor)
        a_long.append(values[2])
        a_lat.append(values[3])
        steer.append(values[4])
        limit.append(values[5])

    if not t_ms:
        raise LessonFileError("NO_SAMPLES")
    _validate_samples(header, t_ms, speed, a_long, a_lat, limit)

    # Identity of the recording: everything except upload metadata, so a box
    # that re-uploads the same lesson (L-1007_retry1) produces the same value.
    identity = "|".join(f"{k}={header[k]}" for k in REQUIRED_HEADERS)
    fingerprint = hashlib.sha256(identity.encode() + b"\n" + body.digest()).hexdigest()

    return Lesson(header, t_ms, speed, a_long, a_lat, steer, limit, fingerprint)


def _validate_header(header: Dict[str, str]) -> None:
    missing = [k for k in REQUIRED_HEADERS if not header.get(k)]
    if missing:
        raise LessonFileError("MISSING_HEADER", ",".join(missing))
    for key in ("lesson_id", "student_id", "instructor_id", "car_id"):
        if not ID_PATTERN.match(header[key]):
            raise LessonFileError("BAD_ID", key)
    if header["lesson_type"] not in LESSON_TYPES:
        raise LessonFileError("BAD_LESSON_TYPE", header["lesson_type"][:20])
    unit = header["speed_unit"].strip().lower()
    unit = SPEED_UNIT_ALIASES.get(unit, unit)
    if unit not in SPEED_UNITS:
        raise LessonFileError("BAD_SPEED_UNIT", header["speed_unit"][:20])
    header["speed_unit"] = unit  # canonical, so "km/h" and "kmh" fingerprint the same
    try:
        rate = float(header["sample_rate_hz"])
    except ValueError:
        raise LessonFileError("BAD_SAMPLE_RATE", header["sample_rate_hz"][:20])
    if not 0 < rate <= 1000:
        raise LessonFileError("BAD_SAMPLE_RATE", header["sample_rate_hz"][:20])
    for key in ("started_at", "uploaded_at"):
        if key in header:
            try:
                header[key] = normalize_timestamp(header[key])
            except ValueError:
                raise LessonFileError("BAD_TIMESTAMP", key)


def _validate_samples(header, t_ms, speed, a_long, a_lat, limit) -> None:
    step = 1000.0 / float(header["sample_rate_hz"])
    for prev, cur in zip(t_ms, t_ms[1:]):
        if cur <= prev:
            raise LessonFileError("NON_MONOTONIC_TIME", f"t_ms {cur}")
    # Metrics count samples, so a file with dropped samples would silently
    # under-report duration and distance. Allow small jitter only.
    expected_span = (len(t_ms) - 1) * step
    actual_span = t_ms[-1] - t_ms[0]
    if abs(actual_span - expected_span) > max(step, 0.01 * expected_span):
        raise LessonFileError("SAMPLE_GAP", f"span {actual_span:.0f}ms, expected {expected_span:.0f}ms")
    if min(speed) < 0 or max(speed) > MAX_SPEED_KMH:
        raise LessonFileError("IMPLAUSIBLE_SPEED")
    if max(abs(v) for v in a_long) > MAX_ABS_ACCEL_MS2 or max(abs(v) for v in a_lat) > MAX_ABS_ACCEL_MS2:
        raise LessonFileError("IMPLAUSIBLE_ACCEL")
    if min(limit) <= 0:
        raise LessonFileError("BAD_SPEED_LIMIT")

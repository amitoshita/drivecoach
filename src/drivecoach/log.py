"""Structured JSON logging and CloudWatch Embedded Metric Format (EMF).

Rule: log identifiers (lesson_id, student_id, s3 key) and outcomes only.
Never log file contents, rows, or anything from students.csv.
"""
from __future__ import annotations

import json
import os
import sys
import time

SERVICE = os.environ.get("SERVICE_NAME", "drivecoach")
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "DriveCoach")

_context = {}


def bind(**fields) -> None:
    """Attach fields (e.g. aws_request_id) to every following log line."""
    _context.clear()
    _context.update(fields)


def _emit(level: str, event: str, fields: dict) -> None:
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "level": level,
              "service": SERVICE, "event": event, **_context, **fields}
    print(json.dumps(record, default=str), file=sys.stdout, flush=True)


def info(event: str, **fields) -> None:
    _emit("INFO", event, fields)


def warning(event: str, **fields) -> None:
    _emit("WARNING", event, fields)


def error(event: str, **fields) -> None:
    _emit("ERROR", event, fields)


def metric(name: str, value: float = 1, unit: str = "Count", **dimensions) -> None:
    """Publish a custom metric through a log line (no PutMetricData call needed)."""
    dims = {"Service": SERVICE, **dimensions}
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                # Always roll up by Service alone too, so alarms need one dimension.
                "Dimensions": [["Service"]] + ([list(dims.keys())] if dimensions else []),
                "Metrics": [{"Name": name, "Unit": unit}],
            }],
        },
        name: value,
        **dims,
    }
    print(json.dumps(record), file=sys.stdout, flush=True)

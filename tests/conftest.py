import csv
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LESSONS_DIR = ROOT / "sample_data" / "lessons"
UPLOAD_ORDER = ROOT / "scripts" / "upload_order.txt"


@pytest.fixture(scope="session")
def rules_v1():
    return json.loads((ROOT / "rules" / "rules_v1.json").read_text())


@pytest.fixture(scope="session")
def student_rows():
    with open(ROOT / "sample_data" / "students.csv", newline="") as f:
        return {row["student_id"]: row for row in csv.DictReader(f)}


@pytest.fixture(scope="session")
def upload_files():
    return [ROOT / line.strip() for line in UPLOAD_ORDER.read_text().splitlines() if line.strip()]


@pytest.fixture(scope="session")
def parsed_lessons(upload_files):
    """Every sample file parsed once, with metrics: {file name: lesson record}."""
    from drivecoach import lesson_file, metrics
    out = {}
    for path in upload_files:
        lesson = lesson_file.parse(path.read_bytes())
        m, events = metrics.compute(lesson)
        out[path.name] = {
            "lesson_id": lesson.lesson_id,
            "student_id": lesson.student_id,
            "lesson_type": lesson.lesson_type,
            "started_at": lesson.started_at,
            "fingerprint": lesson.fingerprint,
            "metrics": m,
            "events": events,
        }
    return out

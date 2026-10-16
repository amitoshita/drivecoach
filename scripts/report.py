"""Print the README results tables by calling the live read API.

Usage: DRIVECOACH_API_URL=... DRIVECOACH_API_KEY=... python scripts/report.py
(or simply: make results)
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get(path):
    req = urllib.request.Request(os.environ["DRIVECOACH_API_URL"].rstrip("/") + path,
                                 headers={"x-api-key": os.environ["DRIVECOACH_API_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def lesson_ids():
    """Lesson ids in field upload order, read from the file headers."""
    ids = []
    for line in (ROOT / "scripts" / "upload_order.txt").read_text().splitlines():
        if not line.strip():
            continue
        for header in (ROOT / line.strip()).read_text().splitlines()[:3]:
            if header.startswith("# lesson_id:"):
                ids.append((Path(line).name, header.split(":", 1)[1].strip()))
    return ids


def main():
    print("### Lessons (in field upload order)\n")
    print("| File | Lesson | Student | Type | Duration (min) | Over limit % | Brakes | Accels | Turns | Max lat g | Score | Result | Rules | Notes |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    students, seen = set(), set()
    for file_name, lesson_id in lesson_ids():
        lesson = get(f"/lessons/{lesson_id}")
        if lesson is None:
            print(f"| {file_name} | {lesson_id} | | | | | | | | | | NOT FOUND | | |")
            continue
        students.add(lesson["student_id"])
        if lesson_id in seen:
            print(f"| {file_name} | {lesson_id} | {lesson['student_id']} | | | | | | | | | duplicate upload, ignored | | |")
            continue
        seen.add(lesson_id)
        m, ev = lesson["metrics"], lesson["evaluation"]
        notes = "; ".join(ev["notes"] + ev["reasons"])
        print(f"| {file_name} | {lesson_id} | {lesson['student_id']} | {lesson['lesson_type']} | "
              f"{m['duration_min']:.2f} | {m['pct_time_over_limit']:.2f} | {m['harsh_brake_count']} | "
              f"{m['harsh_accel_count']} | {m['sharp_turn_count']} | {m['max_lateral_g']:.3f} | "
              f"{m['lesson_score']:.2f} | **{ev['result']}** | {ev['rules_version']} | {notes} |")

    print("\n### Final student state\n")
    print("| Student | Program | Lessons completed | Review count | Next lesson | Status | Final score |")
    print("|---|---|---|---|---|---|---|")
    for sid in sorted(students):
        s = get(f"/students/{sid}")["state"]
        final = "" if s["final_score"] is None else f"{s['final_score']:.2f}"
        print(f"| {sid} | {s['program']} | {s['lessons_completed']} | {s['review_count']} | "
              f"{s['next_lesson_type']} | {s['status']} | {final} |")


if __name__ == "__main__":
    if not os.environ.get("DRIVECOACH_API_URL") or not os.environ.get("DRIVECOACH_API_KEY"):
        sys.exit("set DRIVECOACH_API_URL and DRIVECOACH_API_KEY (or run `make results`)")
    main()

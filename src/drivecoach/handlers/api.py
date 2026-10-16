"""Read API for instructors (API Gateway HTTP API, payload v2).

GET /students/{id}  -> current state, program progress, lesson history, coaching summary
GET /lessons/{id}   -> metrics, event timeline, latest evaluation, full audit trail,
                       short-lived link to the raw file
Responses never contain personal data: it is not stored in this system.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

import boto3

from .. import log, rules as rules_engine, store

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RAW_URL_TTL_SECONDS = int(os.environ.get("RAW_URL_TTL_SECONDS", "300"))
_s3 = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def handler(event, context):
    log.bind(request_id=getattr(context, "aws_request_id", None), function="api")
    route = event.get("routeKey", "")
    item_id = (event.get("pathParameters") or {}).get("id", "")
    if not ID_PATTERN.match(item_id):
        return respond(400, {"error": "invalid id"})
    try:
        if route == "GET /students/{id}":
            body = get_student(item_id)
        elif route == "GET /lessons/{id}":
            body = get_lesson(item_id)
        else:
            return respond(404, {"error": "not found"})
    except Exception as exc:
        log.error("api.failed", route=route, id=item_id, error=type(exc).__name__, detail=str(exc)[:300])
        return respond(500, {"error": "internal error"})
    log.info("api.request", route=route, id=item_id, found=body is not None)
    if body is None:
        return respond(404, {"error": f"{item_id} not found"})
    return respond(200, body)


def respond(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json",
                                              "cache-control": "no-store"},
            "body": json.dumps(body, default=str)}


def get_student(student_id: str):
    student = store.get_student(student_id)
    if student is None:
        return None
    state = student["state"] or student["initial"]
    evaluations = latest_per_lesson(store.evaluations_for_student(student_id, limit=500))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_rules = rules_engine.select(store.load_rule_sets(), today)
    program = current_rules["programs"].get(state["program"], {})

    lessons = [{
        "lesson_id": e["lesson_id"], "started_at": e["started_at"], "lesson_type": e["lesson_type"],
        "result": e["result"], "rule_result": e["rule_result"],
        "lesson_score": e["metrics"]["lesson_score"], "rules_version": e["rules_version"],
        "evaluation_id": e["evaluation_id"],
    } for e in evaluations]

    return {
        "student_id": student_id,
        "state": public_state(state),
        "progress": {"lessons_completed": state["lessons_completed"],
                     "program_lessons": program.get("lessons"),
                     "max_reviews": program.get("max_reviews"),
                     "current_rules_version": current_rules["version"]},
        "state_updated_at": student["updated_at"],
        "lessons": lessons,
        "coaching": coaching_summary(evaluations),
    }


def get_lesson(lesson_id: str):
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        return None
    history = sorted(store.evaluation_history(lesson_id), key=lambda e: e["evaluated_at"])
    latest = history[-1] if history else None
    raw_url = s3().generate_presigned_url(
        "get_object", Params={"Bucket": lesson["s3_bucket"], "Key": lesson["s3_key"]},
        ExpiresIn=RAW_URL_TTL_SECONDS)
    return {
        "lesson_id": lesson_id,
        "student_id": lesson["student_id"],
        "instructor_id": lesson["instructor_id"],
        "car_id": lesson["car_id"],
        "lesson_type": lesson["lesson_type"],
        "started_at": lesson["started_at"],
        "uploaded_at": lesson.get("uploaded_at"),
        "firmware": lesson.get("firmware"),
        "speed_unit_in_file": lesson["speed_unit"],
        "metrics": lesson["metrics"],
        "events": lesson.get("events", []),
        "duplicate_uploads": lesson.get("duplicate_uploads", []),
        "evaluation": public_evaluation(latest) if latest else None,
        "audit_trail": [public_evaluation(e) for e in history],
        "raw_file_url": raw_url,
        "raw_file_url_expires_in_s": RAW_URL_TTL_SECONDS,
    }


def public_state(state):
    if state is None:
        return None
    return {k: state.get(k) for k in ("program", "lessons_completed", "review_count",
                                      "next_lesson_type", "status", "final_score")}


def public_evaluation(e: dict) -> dict:
    return {
        "evaluation_id": e["evaluation_id"], "evaluated_at": e["evaluated_at"],
        "rules_version": e["rules_version"], "rules_sha256": e["rules_sha256"],
        "rule_result": e["rule_result"], "result": e["result"], "reasons": e["reasons"],
        "notes": e["notes"], "metrics": e["metrics"],
        "state_before": public_state(e["state_before"]), "state_after": public_state(e["state_after"]),
        "state_change": {k: v for k, v in e["state_change"].items() if not k.startswith("passed_regular")},
        "trigger": e.get("trigger"), "supersedes": e.get("supersedes"),
    }


def latest_per_lesson(evaluations):
    latest = {}
    for e in evaluations:
        if e["lesson_id"] not in latest or e["evaluated_at"] > latest[e["lesson_id"]]["evaluated_at"]:
            latest[e["lesson_id"]] = e
    return sorted(latest.values(), key=lambda e: (e["started_at"], e["lesson_id"]))


def coaching_summary(evaluations, window: int = 3):
    """What an instructor wants before the next lesson: trend and the one thing to work on."""
    valid = [e for e in evaluations if e["result"] != "INVALID"]
    recent = valid[-window:]
    if not recent:
        return None
    scores = [e["metrics"]["lesson_score"] for e in recent]
    issues = Counter()
    for e in recent:
        m = e["metrics"]
        issues["harsh braking"] += m["harsh_brake_count"]
        issues["harsh acceleration"] += m["harsh_accel_count"]
        issues["sharp turns"] += m["sharp_turn_count"]
        issues["speeding (% of time)"] += round(m["pct_time_over_limit"])
    focus, count = issues.most_common(1)[0]
    return {
        "recent_lessons": [e["lesson_id"] for e in recent],
        "recent_scores": scores,
        "score_trend": round(scores[-1] - scores[0], 2) if len(scores) > 1 else 0,
        "focus_area": focus if count > 0 else None,
        "issue_totals": dict(issues),
    }

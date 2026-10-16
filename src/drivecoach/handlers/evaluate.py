"""Evaluate: FIFO queue (MessageGroupId = student_id) -> this Lambda.

For each student in the batch, rebuild the state from scratch:
    initial state + all stored lessons sorted by started_at, each evaluated
    with the rules in force on its start date.
Then append audit records for every lesson whose outcome is new or changed
(e.g. a late file earlier in the timeline) and save the new state.
"""
from __future__ import annotations

import json

from .. import log, progress, store

# Fields that define an evaluation outcome. If none changed, a replay adds no audit row.
OUTCOME_FIELDS = ("rules_version", "rules_sha256", "rule_result", "result", "state_before", "state_after")


def handler(event, context):
    log.bind(request_id=getattr(context, "aws_request_id", None), function="evaluate")
    records = event.get("Records", [])
    done = set()
    for index, message in enumerate(records):
        try:
            body = json.loads(message["body"])
            student_id = body["student_id"]
        except (ValueError, KeyError, TypeError):
            # Can never succeed: drop it (it would block its FIFO group) but make it visible.
            log.error("evaluate.bad_message", message_id=message.get("messageId"))
            log.metric("ProcessingFailures")
            continue
        if student_id in done:
            continue  # one rebuild already covers every lesson of this student
        try:
            rebuild_student(student_id, trigger=body.get("lesson_id") or body.get("reason", "manual"))
            done.add(student_id)
        except Exception as exc:
            log.error("evaluate.failed", student_id=student_id, error=type(exc).__name__,
                      detail=str(exc)[:300])
            log.metric("ProcessingFailures")
            # FIFO: on the first failure, return this and every later message so
            # ordering within each message group is preserved on retry.
            return {"batchItemFailures": [{"itemIdentifier": m["messageId"]} for m in records[index:]]}
    return {"batchItemFailures": []}


def rebuild_student(student_id: str, trigger: str) -> dict:
    student = store.get_student(student_id) or {"initial": None, "state": None, "lesson_ids": set()}
    lessons = store.get_lessons(student["lesson_ids"])
    if len(lessons) != len(student["lesson_ids"]):
        raise RuntimeError(f"lesson index of {student_id} references missing lessons")

    rule_sets = store.load_rule_sets()
    initial = student["initial"]
    final, steps = progress.replay(initial, lessons, rule_sets)

    written = 0
    for step in steps:
        previous = store.latest_evaluation(step["lesson_id"])
        if previous and all(previous.get(f) == step.get(f) for f in OUTCOME_FIELDS):
            continue
        record = dict(step, student_id=student_id, trigger=trigger,
                      supersedes=previous["evaluation_id"] if previous else None)
        record = store.append_evaluation(record)
        written += 1
        log.info("lesson.evaluated", lesson_id=step["lesson_id"], student_id=student_id,
                 result=step["result"], rules_version=step["rules_version"],
                 reevaluation=previous is not None, evaluation_id=record["evaluation_id"])
        log.metric("Evaluations", result=step["result"])
        if previous:
            log.metric("Reevaluations")

    if initial is None:
        log.warning("student.unknown", student_id=student_id, lessons=len(lessons))
        log.metric("UnknownStudentLessons")
    elif final != student["state"]:
        store.save_state(student_id, final, student["state_version"])
        log.info("student.state_saved", student_id=student_id, status=final["status"],
                 program=final["program"], lessons_completed=final["lessons_completed"])

    return {"state": final, "audit_records_written": written}

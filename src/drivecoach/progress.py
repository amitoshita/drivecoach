"""Student progress: a pure state machine (section 7) and a replay fold.

Student state is never mutated incrementally. It is always rebuilt as
    initial_state + all known lessons of the student, sorted by started_at
which makes the result independent of upload order, retries and late files.
"""
from __future__ import annotations

import copy
from typing import Dict, Iterable, List, Optional, Tuple

from . import rules as rules_engine
from .rules import FAIL, INVALID, PASS, REVIEW

ACTIVE, COMPLETED, INSTRUCTOR_REVIEW = "ACTIVE", "COMPLETED", "INSTRUCTOR_REVIEW"
REGULAR, PRACTICE = "REGULAR", "PRACTICE"

STATE_FIELDS = ("program", "lessons_completed", "review_count", "next_lesson_type",
                "status", "final_score", "passed_regular_count", "passed_regular_score_sum")


PROGRAMS = ("STANDARD", "EXTENDED")
STATUSES = (ACTIVE, COMPLETED, INSTRUCTOR_REVIEW)
LESSON_TYPES = (REGULAR, PRACTICE)


def validate_state(state: dict) -> dict:
    """Fail loudly on an impossible state instead of crashing mid-replay."""
    problems = []
    if state.get("program") not in PROGRAMS:
        problems.append(f"program {state.get('program')!r}")
    if state.get("status") not in STATUSES:
        problems.append(f"status {state.get('status')!r}")
    if state.get("next_lesson_type") not in LESSON_TYPES:
        problems.append(f"next_lesson_type {state.get('next_lesson_type')!r}")
    for key in ("lessons_completed", "review_count"):
        if not isinstance(state.get(key), int) or state[key] < 0:
            problems.append(f"{key} {state.get(key)!r}")
    if problems:
        raise ValueError("invalid student state: " + ", ".join(problems))
    return state


def initial_state(row: dict) -> dict:
    """Build a state from the operational (non-PII) columns of students.csv."""
    return validate_state({
        "program": row["program"],
        "lessons_completed": int(row["lessons_completed"]),
        "review_count": int(row["review_count"]),
        "next_lesson_type": row["next_lesson_type"],
        "status": row["status"],
        "final_score": None,
        # Running totals for final_score: only lessons passed in *this* system.
        "passed_regular_count": 0,
        "passed_regular_score_sum": 0.0,
    })


def apply(state: dict, lesson_type: str, lesson_score: float, result: str,
          rules: dict) -> Tuple[dict, str, List[str]]:
    """Apply one evaluated lesson. Returns (new_state, effective_result, notes)."""
    new = copy.deepcopy(state)
    notes: List[str] = []

    if result == INVALID:
        return new, INVALID, ["INVALID lesson: recorded, no effect on student"]
    if state["status"] != ACTIVE:
        return new, result, [f"student status is {state['status']}: no state change"]
    if lesson_type != state["next_lesson_type"]:
        notes.append(f"lesson_type {lesson_type} but student was due a {state['next_lesson_type']} lesson")

    program = rules["programs"][state["program"]]
    effective = result

    if result == REVIEW:
        new["review_count"] += 1
        new["next_lesson_type"] = PRACTICE
        if new["review_count"] > program["max_reviews"]:
            effective = FAIL
            notes.append(f"review_count {new['review_count']} > max_reviews {program['max_reviews']}: treated as FAIL")

    if effective == PASS:
        new["next_lesson_type"] = REGULAR
        if lesson_type == REGULAR:
            new["lessons_completed"] += 1
            new["passed_regular_count"] += 1
            new["passed_regular_score_sum"] = round(new["passed_regular_score_sum"] + float(lesson_score), 4)
            if new["lessons_completed"] >= program["lessons"]:
                _complete(new)
    elif effective == FAIL:
        # Assumption: after a FAIL the student goes back to REGULAR lessons.
        new["next_lesson_type"] = REGULAR
        if state["program"] == "STANDARD":
            new["program"] = "EXTENDED"
            new["review_count"] = 0
            if new["lessons_completed"] >= rules["programs"]["EXTENDED"]["lessons"]:
                _complete(new)
        else:
            new["status"] = INSTRUCTOR_REVIEW

    return new, effective, notes


def _complete(state: dict) -> None:
    state["status"] = COMPLETED
    if state["passed_regular_count"]:
        state["final_score"] = round(state["passed_regular_score_sum"] / state["passed_regular_count"], 2)


def diff(before: dict, after: dict) -> Dict[str, dict]:
    return {k: {"from": before.get(k), "to": after.get(k)}
            for k in STATE_FIELDS if before.get(k) != after.get(k)}


def lesson_order_key(lesson: dict) -> Tuple[str, str]:
    return (lesson["started_at"], lesson["lesson_id"])


def replay(initial: Optional[dict], lessons: Iterable[dict],
           rule_sets: List[dict]) -> Tuple[Optional[dict], List[dict]]:
    """Fold all lessons of one student, in started_at order.

    `lessons` items need: lesson_id, lesson_type, started_at, metrics.
    Returns (final_state, steps); each step is a complete audit entry.
    An unknown student (initial is None) still gets every lesson evaluated.
    """
    state = validate_state(copy.deepcopy(initial)) if initial is not None else None
    steps = []
    for lesson in sorted(lessons, key=lesson_order_key):
        rules = rules_engine.select(rule_sets, lesson["started_at"])
        result, reasons = rules_engine.evaluate(lesson["metrics"], rules)
        if state is None:
            before = after = None
            effective, notes = result, ["unknown student: lesson evaluated, no state to update"]
        else:
            before = state
            after, effective, notes = apply(state, lesson["lesson_type"],
                                            lesson["metrics"]["lesson_score"], result, rules)
            state = after
        steps.append({
            "lesson_id": lesson["lesson_id"],
            "started_at": lesson["started_at"],
            "lesson_type": lesson["lesson_type"],
            "rules_version": rules["version"],
            "rules_sha256": rules_engine.canonical_hash(rules),
            "metrics": lesson["metrics"],
            "rule_result": result,
            "result": effective,
            "reasons": reasons,
            "notes": notes,
            "state_before": before,
            "state_after": after,
            "state_change": diff(before, after) if before is not None else {},
        })
    return state, steps

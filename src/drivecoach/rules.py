"""Rules engine: rules are data (versioned JSON documents), never code.

A lesson is evaluated with the rule set that was in force when the lesson
*started* (`effective_from` <= started_at). This keeps evaluation
deterministic: replaying an old lesson after v2 is published still uses v1,
so past decisions remain explainable.
"""
from __future__ import annotations

import hashlib
import json
import operator
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from .metrics import METRIC_NAMES

PASS, REVIEW, FAIL, INVALID = "PASS", "REVIEW", "FAIL", "INVALID"

OPERATORS = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt,
    "<=": operator.le, "==": operator.eq, "!=": operator.ne,
}


class RulesError(ValueError):
    pass


def canonical_hash(rules: dict) -> str:
    """Stable hash of a rules document; stored in every audit record."""
    blob = json.dumps(rules, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def validate(rules: dict) -> dict:
    """Reject malformed rules before they can ever be used."""
    for key in ("version", "effective_from", "min_duration_min", "fail_if_any",
                "review_if_any", "programs"):
        if key not in rules:
            raise RulesError(f"missing key: {key}")
    for group in ("fail_if_any", "review_if_any"):
        if not isinstance(rules[group], list):
            raise RulesError(f"{group} must be a list")
        for cond in rules[group]:
            if cond.get("metric") not in METRIC_NAMES:
                raise RulesError(f"{group}: unknown metric {cond.get('metric')!r}")
            if cond.get("op") not in OPERATORS:
                raise RulesError(f"{group}: unknown op {cond.get('op')!r}")
            if not isinstance(cond.get("value"), (int, float)) or isinstance(cond.get("value"), bool):
                raise RulesError(f"{group}: value must be a number")
    try:
        date.fromisoformat(str(rules["effective_from"]))
    except ValueError:
        raise RulesError(f"effective_from must be YYYY-MM-DD, got {rules['effective_from']!r}")
    if not isinstance(rules["min_duration_min"], (int, float)):
        raise RulesError("min_duration_min must be a number")
    programs = rules["programs"]
    if not programs:
        raise RulesError("programs is empty")
    for name, prog in programs.items():
        if not isinstance(prog.get("lessons"), int) or prog["lessons"] <= 0:
            raise RulesError(f"program {name}: lessons must be a positive int")
        if not isinstance(prog.get("max_reviews"), int) or prog["max_reviews"] < 0:
            raise RulesError(f"program {name}: max_reviews must be a non-negative int")
    for name in ("STANDARD", "EXTENDED"):
        if name not in programs:
            raise RulesError(f"program {name} is required by the state machine")
    return rules


def select(rule_sets: Iterable[dict], started_at: str) -> dict:
    """Pick the newest rule set whose effective_from is on/before the lesson start."""
    lesson_day = started_at[:10]
    candidates = [r for r in rule_sets if str(r["effective_from"])[:10] <= lesson_day]
    if not candidates:
        raise RulesError(f"no rules in force on {lesson_day}")
    return max(candidates, key=lambda r: (str(r["effective_from"]), r["version"]))


def _matches(conditions: List[dict], metrics: Dict[str, float]) -> List[str]:
    hits = []
    for cond in conditions:
        value = metrics[cond["metric"]]
        if OPERATORS[cond["op"]](float(value), float(cond["value"])):
            hits.append(f"{cond['metric']} {cond['op']} {cond['value']} (was {value})")
    return hits


def evaluate(metrics: Dict[str, float], rules: dict) -> Tuple[str, List[str]]:
    """Return (result, reasons) following the order in section 6."""
    duration = float(metrics["duration_min"])
    if duration < float(rules["min_duration_min"]):
        return INVALID, [f"duration_min < {rules['min_duration_min']} (was {metrics['duration_min']})"]
    fails = _matches(rules["fail_if_any"], metrics)
    if fails:
        return FAIL, fails
    reviews = _matches(rules["review_if_any"], metrics)
    if reviews:
        return REVIEW, reviews
    return PASS, []


def find(rule_sets: Iterable[dict], version: str) -> Optional[dict]:
    return next((r for r in rule_sets if r["version"] == version), None)

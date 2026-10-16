"""DynamoDB access. Table names come from environment variables set by Terraform."""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from . import rules as rules_engine

_dynamodb = None
_rules_cache = {"at": 0.0, "value": None}
RULES_CACHE_SECONDS = int(os.environ.get("RULES_CACHE_SECONDS", "60"))


def _table(env_name: str):
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb.Table(os.environ[env_name])


def lessons_table():
    return _table("LESSONS_TABLE")


def evaluations_table():
    return _table("EVALUATIONS_TABLE")


def students_table():
    return _table("STUDENTS_TABLE")


def rules_table():
    return _table("RULES_TABLE")


def to_dynamo(value):
    """Python -> DynamoDB (floats must be Decimal)."""
    return json.loads(json.dumps(value), parse_float=Decimal)


def from_dynamo(value):
    """DynamoDB -> plain Python (Decimal -> int/float)."""
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _query_all(table, **kwargs) -> List[dict]:
    items = []
    while True:
        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


# ---------------- lessons ----------------

class DuplicateLesson(Exception):
    def __init__(self, existing: dict):
        super().__init__(existing.get("lesson_id"))
        self.existing = existing


def put_lesson_once(item: dict) -> None:
    """Insert a lesson; raise DuplicateLesson if that lesson_id already exists."""
    try:
        lessons_table().put_item(Item=to_dynamo(item),
                                 ConditionExpression="attribute_not_exists(lesson_id)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        existing = lessons_table().get_item(Key={"lesson_id": item["lesson_id"]},
                                            ConsistentRead=True)["Item"]
        raise DuplicateLesson(from_dynamo(existing))


def record_duplicate_upload(lesson_id: str, s3_key: str, uploaded_at: Optional[str]) -> None:
    # duplicate_keys (a string set) makes the update idempotent under redelivery.
    try:
        lessons_table().update_item(
            Key={"lesson_id": lesson_id},
            UpdateExpression=("SET duplicate_uploads = list_append(if_not_exists(duplicate_uploads, :empty), :new) "
                              "ADD duplicate_keys :keyset"),
            ConditionExpression="attribute_not_exists(duplicate_keys) OR NOT contains(duplicate_keys, :key)",
            ExpressionAttributeValues={
                ":empty": [], ":key": s3_key, ":keyset": {s3_key},
                ":new": [{"s3_key": s3_key, "uploaded_at": uploaded_at, "seen_at": now_iso()}],
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def get_lesson(lesson_id: str) -> Optional[dict]:
    item = lessons_table().get_item(Key={"lesson_id": lesson_id}).get("Item")
    return from_dynamo(item) if item else None


def lessons_for_student(student_id: str) -> List[dict]:
    items = _query_all(lessons_table(), IndexName="by_student",
                       KeyConditionExpression=Key("student_id").eq(student_id))
    # GSI reads are eventually consistent: the lesson that triggered this
    # evaluation may be missing. Callers re-read it by key (see evaluate.py).
    return [from_dynamo(i) for i in items]


# ---------------- evaluations (append-only audit) ----------------

def latest_evaluation(lesson_id: str) -> Optional[dict]:
    page = evaluations_table().query(KeyConditionExpression=Key("lesson_id").eq(lesson_id),
                                     ScanIndexForward=False, Limit=1, ConsistentRead=True)
    items = page.get("Items", [])
    return from_dynamo(items[0]) if items else None


def evaluation_history(lesson_id: str) -> List[dict]:
    items = _query_all(evaluations_table(), KeyConditionExpression=Key("lesson_id").eq(lesson_id))
    return [from_dynamo(i) for i in items]


def evaluations_for_student(student_id: str, limit: int = 50) -> List[dict]:
    page = evaluations_table().query(IndexName="by_student", Limit=limit, ScanIndexForward=False,
                                     KeyConditionExpression=Key("student_id").eq(student_id))
    return [from_dynamo(i) for i in page.get("Items", [])]


def append_evaluation(record: dict) -> dict:
    record = dict(record, evaluated_at=now_iso(),
                  evaluation_id=f"E-{uuid.uuid4().hex[:12]}")
    evaluations_table().put_item(
        Item=to_dynamo(record),
        ConditionExpression="attribute_not_exists(evaluated_at)",  # never overwrite history
    )
    return record


# ---------------- students ----------------

def get_student(student_id: str) -> Optional[dict]:
    """Return {"initial": ..., "state": ..., "state_version": ...} or None if unknown."""
    items = _query_all(students_table(), KeyConditionExpression=Key("student_id").eq(student_id),
                       ConsistentRead=True)
    if not items:
        return None
    records = {i["record"]: from_dynamo(i) for i in items}
    initial = records.get("INITIAL")
    current = records.get("STATE")
    return {
        "initial": initial["state"] if initial else None,
        "state": current["state"] if current else None,
        "state_version": current["state_version"] if current else 0,
        "updated_at": current.get("updated_at") if current else None,
    }


def save_state(student_id: str, state: dict, expected_version: int) -> None:
    """Optimistic lock: fail if another writer changed the state meanwhile."""
    students_table().put_item(
        Item=to_dynamo({"student_id": student_id, "record": "STATE", "state": state,
                        "state_version": expected_version + 1, "updated_at": now_iso()}),
        ConditionExpression="attribute_not_exists(student_id) OR state_version = :v",
        ExpressionAttributeValues={":v": expected_version},
    )


# ---------------- rules ----------------

def load_rule_sets(force: bool = False) -> List[dict]:
    """All published rule sets, cached briefly so a new version is picked up
    within RULES_CACHE_SECONDS without any redeploy."""
    if not force and _rules_cache["value"] is not None and time.time() - _rules_cache["at"] < RULES_CACHE_SECONDS:
        return _rules_cache["value"]
    items = rules_table().scan().get("Items", [])
    rule_sets = []
    for item in items:
        doc = json.loads(item["document"])
        rules_engine.validate(doc)
        if doc["version"] != item["version"]:
            raise rules_engine.RulesError(f"rules item {item['version']} contains version {doc['version']}")
        rule_sets.append(doc)
    _rules_cache.update(at=time.time(), value=rule_sets)
    return rule_sets

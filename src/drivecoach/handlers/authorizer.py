"""HTTP API Lambda authorizer: checks the x-api-key header against an SSM SecureString.

The key is generated at deploy time (Terraform random_password) and never
committed. API Gateway caches the decision per key for a few minutes.
"""
from __future__ import annotations

import hmac
import os
import time

import boto3

from .. import log

CACHE_SECONDS = 300
_cache = {"at": 0.0, "key": None}
_ssm = None


def expected_key() -> str:
    global _ssm
    if _cache["key"] is None or time.time() - _cache["at"] > CACHE_SECONDS:
        if _ssm is None:
            _ssm = boto3.client("ssm")
        param = _ssm.get_parameter(Name=os.environ["API_KEY_PARAM"], WithDecryption=True)
        _cache.update(key=param["Parameter"]["Value"], at=time.time())
    return _cache["key"]


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    supplied = headers.get("x-api-key", "")
    allowed = bool(supplied) and hmac.compare_digest(supplied.encode(), expected_key().encode())
    if not allowed:
        log.warning("api.unauthorized", route=event.get("routeKey"),
                    source_ip=(event.get("requestContext") or {}).get("http", {}).get("sourceIp"))
        log.metric("UnauthorizedRequests")
    return {"isAuthorized": allowed}

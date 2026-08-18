"""Reusable response privacy assertions for Web backend test phases."""

from __future__ import annotations

import json
import re
import unittest
from typing import Any


WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|file://)")
SENSITIVE_MARKERS = (
    "api_key",
    "secret",
    "credential_env_name",
    "authorization",
    ".env",
    "raw_error",
)


def assert_public_payload(testcase: unittest.TestCase, payload: Any) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    testcase.assertIsNone(WINDOWS_ABSOLUTE_PATH.search(serialized))
    lowered = serialized.casefold()
    for marker in SENSITIVE_MARKERS:
        testcase.assertNotIn(marker, lowered)


def assert_boolean_leaves(testcase: unittest.TestCase, value: Any) -> None:
    if isinstance(value, dict):
        testcase.assertTrue(value)
        for nested in value.values():
            assert_boolean_leaves(testcase, nested)
        return
    testcase.assertIsInstance(value, bool)

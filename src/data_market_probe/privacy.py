"""Conservative masking for contact details exposed by query APIs."""

from __future__ import annotations

import re
from typing import Any


_EMAIL = re.compile(r"(?<![A-Za-z0-9_.+-])([A-Za-z0-9_.+-]{1,64})@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9_.-])")
_MOBILE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)(\d{6})(?:\d{8})(\d{3}[0-9Xx])(?!\d)")


def redact_text(value: str) -> str:
    value = _EMAIL.sub(lambda match: f"{match.group(1)[:2]}***@{match.group(2)}", value)
    value = _MOBILE.sub(lambda match: f"{match.group(1)}****{match.group(3)}", value)
    return _ID_CARD.sub(lambda match: f"{match.group(1)}********{match.group(2)}", value)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_sensitive(child) for key, child in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(child) for child in value]
    return value

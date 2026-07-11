"""Auditable acceptance reports for all registered source platforms."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import init_database, session_factory, session_scope
from .query import coverage_matrix


TERMINAL_CONCLUSIONS = {"COMPLETE", "BLOCKED", "OFFLINE", "OUT_OF_SCOPE"}


def build_acceptance_report(settings: object, output: Path | None = None) -> dict[str, Any]:
    init_database(settings)
    factory = session_factory(settings)
    with session_scope(factory) as session:
        platforms = coverage_matrix(session)
    counts: dict[str, int] = {}
    for platform in platforms:
        conclusion = str(platform.get("conclusion") or "UNKNOWN").upper()
        counts[conclusion] = counts.get(conclusion, 0) + 1
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_count": len(platforms),
        "all_sources_registered": len(platforms) == 38,
        "all_sources_audited": len(platforms) == 38 and all(
            str(row.get("conclusion") or "").upper() in TERMINAL_CONCLUSIONS
            for row in platforms
        ),
        "counts": counts,
        "platforms": platforms,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

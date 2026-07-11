"""Optional LLM assistance for pending, low-confidence classifications.

The model is advisory only: it updates the review proposal and never changes a
published catalog version.  An administrator must still accept the proposal.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy import select

from .database import init_database, session_factory, session_scope
from .models import CatalogItemVersion, ClassificationReview
from .taxonomy import PRODUCT_TYPE_KEYWORDS


ALLOWED_PRODUCT_TYPES = {value for value, _keywords in PRODUCT_TYPE_KEYWORDS} | {"other"}


def _json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.rsplit("```", 1)[0]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("LLM classification response must be a JSON object")
    return value


def enrich_pending_reviews(settings: object) -> dict[str, int | str]:
    """Ask an OpenAI-compatible endpoint to improve pending proposals."""

    if not bool(getattr(settings, "llm_enabled", False)):
        return {"status": "disabled", "processed": 0, "updated": 0, "failed": 0}
    api_key = str(getattr(settings, "llm_api_key", ""))
    model = str(getattr(settings, "llm_model", ""))
    if not api_key or not model:
        return {"status": "not_configured", "processed": 0, "updated": 0, "failed": 0}

    init_database(settings)
    factory = session_factory(settings)
    batch_size = int(getattr(settings, "llm_review_batch_size", 50))
    with session_scope(factory) as session:
        rows = session.scalars(
            select(ClassificationReview)
            .where(ClassificationReview.status == "pending")
            .order_by(ClassificationReview.created_at)
            .limit(batch_size)
        ).all()
        inputs = []
        for review in rows:
            version = session.get(CatalogItemVersion, review.version_id)
            if version is not None and review.field_name == "product_type":
                inputs.append((review.id, version.name, version.description[:2000], version.product_type_raw))

    processed = updated = failed = 0
    endpoint = str(getattr(settings, "llm_base_url", "https://api.openai.com/v1")).rstrip("/") + "/chat/completions"
    timeout = float(getattr(settings, "llm_timeout_seconds", 30.0))
    allowed = sorted(ALLOWED_PRODUCT_TYPES)
    with httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}) as client:
        for review_id, name, description, raw_type in inputs:
            processed += 1
            try:
                response = client.post(
                    endpoint,
                    json={
                        "model": model,
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": "Classify a Chinese data-market catalog item. Return JSON only with product_type and confidence (0..1)."},
                            {"role": "user", "content": json.dumps({"allowed": allowed, "name": name, "raw_type": raw_type, "description": description}, ensure_ascii=False)},
                        ],
                    },
                )
                response.raise_for_status()
                payload = _json_object(response.json()["choices"][0]["message"]["content"])
                proposed = str(payload.get("product_type", ""))
                confidence = float(payload.get("confidence", 0.0))
                if proposed not in ALLOWED_PRODUCT_TYPES or not 0.0 <= confidence <= 1.0:
                    raise ValueError("LLM returned an unsupported classification")
                with session_scope(factory) as session:
                    review = session.get(ClassificationReview, review_id)
                    if review is not None and review.status == "pending":
                        review.proposed_value = proposed
                        review.confidence = confidence
                        review.decision_note = "llm_assisted"
                        updated += 1
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                failed += 1
    return {"status": "success", "processed": processed, "updated": updated, "failed": failed}

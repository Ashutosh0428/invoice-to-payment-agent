from __future__ import annotations

import json
import re
from typing import TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from invoice_agent.core.config import get_config
from invoice_agent.core.errors import ExtractionError
from invoice_agent.llm.provider import get_chat_model

T = TypeVar("T", bound=BaseModel)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _coerce_json(text: str) -> dict[str, object]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if match is None:
        raise ExtractionError("model returned no JSON object", details={"raw": text[:800]})
    return json.loads(match.group(0))


async def extract_structured(
    system_prompt: str,
    user_prompt: str,
    schema: type[T],
    *,
    max_retries: int | None = None,
) -> T:
    """Ask the model for JSON matching `schema`, retrying with the validation error attached.

    Ollama's JSON mode guarantees syntactic JSON, never schema conformance; the retry loop is
    what closes that gap without a tool-calling dependency.
    """
    cfg = get_config().llm
    retries = cfg.max_extraction_retries if max_retries is None else max_retries
    model = get_chat_model(json_mode=True)

    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    base_messages = [
        (
            "system",
            f"{system_prompt}\n\nReturn a single JSON object matching this schema:\n{schema_json}",
        ),
        ("human", user_prompt),
    ]

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        messages = list(base_messages)
        if last_error is not None:
            messages.append(
                (
                    "human",
                    f"Your previous response failed validation: {last_error}. "
                    "Return corrected JSON only, no prose.",
                )
            )
        response = await model.ainvoke(messages)
        raw = response.content if isinstance(response.content, str) else str(response.content)
        try:
            return schema.model_validate(_coerce_json(raw))
        except (ValidationError, json.JSONDecodeError, ExtractionError) as exc:
            last_error = exc
            logger.warning(
                "Structured extraction attempt {}/{} failed: {}", attempt + 1, retries + 1, exc
            )

    raise ExtractionError(
        f"failed to obtain valid {schema.__name__} after {retries + 1} attempts",
        details={"last_error": str(last_error)},
    )

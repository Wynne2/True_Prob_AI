"""
OpenAI Responses API client for the AI Advisor.

Uses the official ``openai`` SDK when it is importable; otherwise it calls the
same ``POST /v1/responses`` endpoint with ``requests`` using identical
parameters. Either way:

- the configured model is verified with ``GET /v1/models/{id}`` and the call
  fails loudly if it is unavailable (we never silently switch models);
- structured output is requested with a strict JSON schema;
- token usage and latency are captured;
- the API key never appears in logs, errors or saved files.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from config import OpenAIConfig, get_openai_config
from utils.api_debug import redact_sensitive_text

logger = logging.getLogger(__name__)

VALID_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
SCHEMA_NAME = "advisor_response"


class AdvisorLLMError(RuntimeError):
    """Any failure talking to the LLM (missing key, model unavailable, timeout, API error)."""


@dataclass
class LLMResult:
    """Text and metadata from one Responses API call."""
    text: str
    model: str
    response_id: str = ""
    status: str = ""
    latency_s: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    transport: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _sdk_available() -> bool:
    try:
        import openai  # noqa: F401
    except Exception:
        return False
    return True


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return {}
    out = {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }
    details = usage.get("output_tokens_details") or {}
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        out["reasoning_tokens"] = int(details["reasoning_tokens"])
    cached = (usage.get("input_tokens_details") or {})
    if isinstance(cached, dict) and cached.get("cached_tokens") is not None:
        out["cached_tokens"] = int(cached["cached_tokens"])
    return out


def extract_output_text(raw: dict) -> str:
    """Concatenate ``output_text`` parts of message items; raise on refusal."""
    if isinstance(raw.get("output_text"), str) and raw["output_text"]:
        return raw["output_text"]
    parts: list[str] = []
    for item in raw.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            ctype = content.get("type")
            if ctype == "output_text":
                parts.append(content.get("text") or "")
            elif ctype == "refusal":
                raise AdvisorLLMError(f"Model refused: {content.get('refusal', '')[:300]}")
    return "".join(parts)


class OpenAIAdvisorClient:
    """Thin wrapper around the Responses API with a model-availability check."""

    def __init__(
        self,
        config: Optional[OpenAIConfig] = None,
        *,
        transport: str = "auto",
        session: Optional[requests.Session] = None,
    ) -> None:
        self.config = config or get_openai_config()
        if transport not in ("auto", "sdk", "http"):
            raise ValueError(f"Unknown transport {transport!r}")
        if transport == "auto":
            transport = "sdk" if _sdk_available() else "http"
        self.transport = transport
        self._session = session
        self._sdk_client = None
        self._model_verified = False

    # -- helpers -----------------------------------------------------------

    def _require_key(self) -> str:
        key = self.config.api_key
        if not key:
            raise AdvisorLLMError(
                "OPENAI_API_KEY is not set. Add it to .env to enable the AI Advisor "
                "(or run with --no-llm for the deterministic report)."
            )
        return key

    def _redact(self, text: str) -> str:
        return redact_sensitive_text(str(text), [self.config.api_key or ""])

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._require_key()}",
            "Content-Type": "application/json",
        }

    def _http(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _sdk(self):
        if self._sdk_client is None:
            from openai import OpenAI

            self._sdk_client = OpenAI(
                api_key=self._require_key(),
                base_url=self.config.base_url,
                timeout=float(self.config.timeout),
                max_retries=1,
            )
        return self._sdk_client

    def request_params(self, system_prompt: str, messages: list[dict], schema: dict) -> dict:
        """Exact parameters sent to ``responses.create`` / ``POST /responses``."""
        effort = self.config.reasoning_effort
        if effort not in VALID_EFFORTS:
            raise AdvisorLLMError(
                f"OPENAI_REASONING_EFFORT={effort!r} is invalid; use one of {', '.join(VALID_EFFORTS)}"
            )
        return {
            "model": self.config.model,
            "input": [{"role": "system", "content": system_prompt}, *messages],
            "reasoning": {"effort": effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "schema": schema,
                    "strict": True,
                }
            },
            "max_output_tokens": int(self.config.max_output_tokens),
            "store": False,
        }

    # -- public API --------------------------------------------------------

    def verify_model(self) -> None:
        """Fail loudly if the configured model is not available to this key."""
        if self._model_verified:
            return
        self._require_key()
        model = self.config.model
        try:
            if self.transport == "sdk":
                self._sdk().models.retrieve(model)
            else:
                resp = self._http().get(
                    f"{self.config.base_url}/models/{model}",
                    headers=self._headers(),
                    timeout=min(30, self.config.timeout),
                )
                if resp.status_code == 404:
                    raise AdvisorLLMError(f"Model {model!r} is not available for this API key (404).")
                if resp.status_code == 401:
                    raise AdvisorLLMError("OpenAI rejected the API key (401). Check OPENAI_API_KEY.")
                if not resp.ok:
                    raise AdvisorLLMError(
                        f"Model check failed: HTTP {resp.status_code} {self._redact(resp.text[:300])}"
                    )
        except AdvisorLLMError:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status == 404:
                raise AdvisorLLMError(f"Model {model!r} is not available for this API key (404).") from None
            if status == 401:
                raise AdvisorLLMError("OpenAI rejected the API key (401). Check OPENAI_API_KEY.") from None
            raise AdvisorLLMError(f"Model check failed: {self._redact(exc)}") from None
        self._model_verified = True
        logger.info("OpenAI model %s verified (%s transport)", model, self.transport)

    def complete(self, system_prompt: str, messages: list[dict], schema: dict) -> LLMResult:
        """Run one structured-output request and return the text plus metadata."""
        params = self.request_params(system_prompt, messages, schema)
        start = time.perf_counter()
        try:
            if self.transport == "sdk":
                response = self._sdk().responses.create(**params, timeout=float(self.config.timeout))
                raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)
            else:
                resp = self._http().post(
                    f"{self.config.base_url}/responses",
                    headers=self._headers(),
                    data=json.dumps(params),
                    timeout=self.config.timeout,
                )
                if not resp.ok:
                    raise AdvisorLLMError(
                        f"OpenAI API error HTTP {resp.status_code}: {self._redact(resp.text[:500])}"
                    )
                raw = resp.json()
        except AdvisorLLMError:
            raise
        except requests.Timeout:
            raise AdvisorLLMError(f"OpenAI request timed out after {self.config.timeout}s") from None
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name:
                raise AdvisorLLMError(f"OpenAI request timed out after {self.config.timeout}s") from None
            raise AdvisorLLMError(f"OpenAI request failed ({name}): {self._redact(exc)}") from None
        latency = time.perf_counter() - start

        status = str(raw.get("status") or "")
        if raw.get("error"):
            raise AdvisorLLMError(f"OpenAI returned an error: {self._redact(json.dumps(raw['error']))[:500]}")
        if status == "incomplete":
            reason = (raw.get("incomplete_details") or {}).get("reason", "unknown")
            raise AdvisorLLMError(
                f"OpenAI response incomplete ({reason}); consider raising OPENAI_MAX_OUTPUT_TOKENS"
            )

        result = LLMResult(
            text=extract_output_text(raw),
            model=str(raw.get("model") or self.config.model),
            response_id=str(raw.get("id") or ""),
            status=status,
            latency_s=round(latency, 3),
            usage=_usage_dict(raw.get("usage")),
            transport=self.transport,
            raw=raw,
        )
        logger.info(
            "OpenAI %s responded in %.2fs (in=%s out=%s reasoning=%s tokens)",
            result.model, result.latency_s,
            result.usage.get("input_tokens"), result.usage.get("output_tokens"),
            result.usage.get("reasoning_tokens"),
        )
        return result

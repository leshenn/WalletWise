from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from .ai_response import WALLETWISE_RESPONSE_JSON_SCHEMA, WalletWiseAIResponse


class AIConfigurationError(RuntimeError):
    pass


class AIProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIServiceResult:
    answer: dict[str, Any]
    usage: dict[str, Any]
    model: str
    provider_payload: dict[str, Any] | None = None
    raw_answer: str | None = None
    retry_used: bool = False


class GroqAIService:
    """Token-conscious Groq client kept behind the WalletWise backend.

    The browser never receives the API key. The provider receives:
      1. one small static runtime prompt,
      2. one tiny intent-specific instruction block,
      3. a short recent conversation window,
      4. the minimal sanitised financial context for that intent,
      5. the current question.

    The large developer prompt remains in the project as documentation, but is
    deliberately not sent on every request.
    """

    DEFAULT_MODEL = "openai/gpt-oss-120b"
    DEFAULT_URL = "https://api.groq.com/openai/v1/chat/completions"
    RUNTIME_PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "WALLETWISE_RUNTIME_PROMPT.md"
    INTENT_PROMPTS_FILE = Path(__file__).resolve().parent / "prompts" / "WALLETWISE_INTENT_PROMPTS.md"
    HISTORY_MESSAGE_LIMIT = 6  # three recent exchanges for multi-turn financial context
    USER_HISTORY_CHAR_LIMIT = 450
    ASSISTANT_HISTORY_CHAR_LIMIT = 550

    @classmethod
    def _env_bool(cls, name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().casefold() in {"1", "true", "yes", "on"}

    @classmethod
    def _max_completion_tokens(cls) -> int:
        raw = (os.getenv("WALLETWISE_AI_MAX_COMPLETION_TOKENS") or "1400").strip()
        try:
            return max(600, min(int(raw), 2400))
        except ValueError:
            return 1400

    @classmethod
    def _retry_completion_tokens(cls) -> int:
        raw = (os.getenv("WALLETWISE_AI_RETRY_COMPLETION_TOKENS") or "2000").strip()
        try:
            return max(cls._max_completion_tokens(), min(int(raw), 3000))
        except ValueError:
            return max(cls._max_completion_tokens(), 2000)

    @classmethod
    @lru_cache(maxsize=1)
    def _load_runtime_prompt(cls) -> str:
        try:
            prompt = cls.RUNTIME_PROMPT_FILE.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise AIConfigurationError(
                f"WalletWise runtime prompt could not be loaded: {cls.RUNTIME_PROMPT_FILE}"
            ) from exc
        if not prompt:
            raise AIConfigurationError("WalletWise runtime prompt is empty.")
        return prompt

    @classmethod
    @lru_cache(maxsize=1)
    def _load_intent_prompts(cls) -> dict[str, str]:
        try:
            text = cls.INTENT_PROMPTS_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            raise AIConfigurationError(
                f"WalletWise intent prompt file could not be loaded: {cls.INTENT_PROMPTS_FILE}"
            ) from exc

        blocks: dict[str, str] = {}
        pattern = re.compile(r"^##\s+([a-z_]+)\s*$\n(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
        for match in pattern.finditer(text):
            blocks[match.group(1).strip()] = match.group(2).strip()

        if "general_finance" not in blocks:
            raise AIConfigurationError("WalletWise intent prompt file is missing the general_finance block.")
        return blocks

    @classmethod
    def _intent_prompt(cls, intent: str) -> str:
        prompts = cls._load_intent_prompts()
        return prompts.get(intent) or prompts["general_finance"]

    @classmethod
    def status(cls) -> dict[str, Any]:
        key = (os.getenv("GROQ_API_KEY") or "").strip()
        return {
            "configured": bool(key),
            "provider": "Groq",
            "model": (os.getenv("GROQ_MODEL") or cls.DEFAULT_MODEL).strip(),
            "structuredOutput": True,
            "debugLogging": False,
            "runtimePromptFile": cls.RUNTIME_PROMPT_FILE.name,
            "intentPromptFile": cls.INTENT_PROMPTS_FILE.name,
            "maxCompletionTokens": cls._max_completion_tokens(),
            "retryCompletionTokens": cls._retry_completion_tokens(),
            "historyMessageLimit": cls.HISTORY_MESSAGE_LIMIT,
            "localAnswers": True,
            "hybridMode": True,
            "aiRouterEnabled": cls._env_bool("WALLETWISE_ROUTER_AI_ENABLED", default=True),
            "routerModel": (os.getenv("GROQ_ROUTER_MODEL") or os.getenv("GROQ_MODEL") or cls.DEFAULT_MODEL).strip(),
            "routerMaxTokens": int((os.getenv("WALLETWISE_ROUTER_MAX_TOKENS") or "320").strip() or 320),
            "routerConfidenceThreshold": float((os.getenv("WALLETWISE_ROUTER_CONFIDENCE_THRESHOLD") or "0.78").strip() or 0.78),
            "terminalTrace": cls._env_bool("WALLETWISE_TRACE", default=True),
        }

    @classmethod
    def _compact_assistant_history(cls, content: str) -> str:
        """Keep only the pieces needed for follow-up questions.

        The frontend may send the previous structured answer as JSON. Re-sending
        the whole answer wastes tokens, so keep only its conclusion and a few
        key figures.
        """
        try:
            value = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content[: cls.ASSISTANT_HISTORY_CHAR_LIMIT]

        if not isinstance(value, dict):
            return content[: cls.ASSISTANT_HISTORY_CHAR_LIMIT]

        metrics = []
        for item in (value.get("key_metrics") or [])[:3]:
            if isinstance(item, dict):
                metrics.append({
                    "label": item.get("label"),
                    "value": item.get("value"),
                    "change": item.get("change"),
                })

        compact = {
            "title": value.get("title"),
            "summary": value.get("summary"),
            "status": value.get("status"),
            "key_metrics": metrics,
            "next_step": value.get("next_step"),
            "follow_up_question": value.get("follow_up_question"),
        }
        return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))[: cls.ASSISTANT_HISTORY_CHAR_LIMIT]

    @classmethod
    def _prepare_history(cls, history: list[dict] | None) -> list[dict[str, str]]:
        clean: list[dict[str, str]] = []
        for item in (history or [])[-cls.HISTORY_MESSAGE_LIMIT :]:
            role = item.get("role")
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            if role == "assistant":
                content = cls._compact_assistant_history(content)
            else:
                content = content[: cls.USER_HISTORY_CHAR_LIMIT]
            clean.append({"role": role, "content": content})
        return clean

    @staticmethod
    def _approx_tokens(text: str) -> int:
        # Debug-only estimate. Actual provider usage is printed after the call.
        if not text:
            return 0
        return max(1, math.ceil(len(text) / 4))

    @classmethod
    def _debug_request(cls, **_: Any) -> None:
        # Removed: Wally now has one unified semantic-routing trace.
        return

    @classmethod
    def _debug_response(cls, **_: Any) -> None:
        # Removed: Wally now has one unified semantic-routing trace.
        return

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> AIProviderError:
        detail = ""
        try:
            body = response.json()
            detail = body.get("error", {}).get("message") or body.get("message") or ""
        except Exception:
            detail = response.text[:500]
        suffix = f" {detail}" if detail else ""
        return AIProviderError(f"Groq request failed ({response.status_code}).{suffix}".strip())

    @staticmethod
    def _is_completion_truncation(response: httpx.Response) -> bool:
        if response.status_code < 400:
            return False
        try:
            text = json.dumps(response.json(), ensure_ascii=False).casefold()
        except Exception:
            text = response.text.casefold()
        return (
            "max completion tokens reached" in text
            or "max_completion_tokens" in text
            or "failed_generation" in text and "truncated" in text
        )

    @classmethod
    def ask(
        cls,
        *,
        question: str,
        context: dict[str, Any],
        history: list[dict] | None = None,
        conversation_summary: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> AIServiceResult:
        api_key = (os.getenv("GROQ_API_KEY") or "").strip()
        if not api_key:
            raise AIConfigurationError(
                "Groq is not configured. Add GROQ_API_KEY to backend/.env and restart the backend."
            )

        model = (os.getenv("GROQ_MODEL") or cls.DEFAULT_MODEL).strip()
        api_url = (os.getenv("GROQ_API_URL") or cls.DEFAULT_URL).strip()
        runtime_prompt = cls._load_runtime_prompt()
        intent = str(context.get("intent") or "general_finance")
        intent_prompt = cls._intent_prompt(intent)
        clean_history = cls._prepare_history(history)

        messages: list[dict[str, str]] = [
            {"role": "system", "content": runtime_prompt},
            {"role": "system", "content": f"INTENT_INSTRUCTIONS:\n{intent_prompt}"},
        ]
        if conversation_summary:
            messages.append({
                "role": "system",
                "content": (
                    "CONVERSATION_SUMMARY:\n"
                    + json.dumps(conversation_summary, ensure_ascii=False, separators=(",", ":"))
                ),
            })
        messages.extend(clean_history)

        context_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        messages.append({
            "role": "user",
            "content": (
                "FINANCIAL_CONTEXT:\n"
                f"{context_json}\n\n"
                "USER_QUESTION:\n"
                f"{question.strip()}"
            ),
        })

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "reasoning_effort": "low",
            "max_completion_tokens": cls._max_completion_tokens(),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "walletwise_financial_answer",
                    "strict": True,
                    "schema": WALLETWISE_RESPONSE_JSON_SCHEMA,
                },
            },
        }

        # The unified Wally routing trace prints the provider payload from the
        # returned AIServiceResult. ai_service itself stays silent.
        sent_payload = payload
        retry_used = False

        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    api_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

                # Structured JSON can occasionally be cut off before the required
                # closing fields. Retry once only for that specific provider error.
                if cls._is_completion_truncation(response):
                    retry_tokens = cls._retry_completion_tokens()
                    if retry_tokens > payload["max_completion_tokens"]:
                        retry_payload = {**payload, "max_completion_tokens": retry_tokens}
                        sent_payload = retry_payload
                        retry_used = True
                        response = client.post(
                            api_url,
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                            },
                            json=retry_payload,
                        )
        except httpx.RequestError as exc:
            raise AIProviderError(f"Could not reach Groq: {exc}") from exc

        if response.status_code >= 400:
            raise cls._provider_error(response)

        try:
            body = response.json()
            raw_answer = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderError("Groq returned an unexpected response envelope.") from exc

        if not str(raw_answer).strip():
            raise AIProviderError("Groq returned an empty answer.")

        try:
            parsed_json = json.loads(str(raw_answer))
        except json.JSONDecodeError as exc:
            raise AIProviderError(
                "Groq returned a response that was not valid JSON. Check the terminal debug output."
            ) from exc

        try:
            validated = WalletWiseAIResponse.model_validate(parsed_json)
        except ValidationError as exc:
            raise AIProviderError(
                "Groq returned JSON that did not match the WalletWise response contract. "
                "Check the terminal debug output."
            ) from exc

        result = validated.model_dump(mode="json")
        usage = body.get("usage") if isinstance(body, dict) else None
        return AIServiceResult(
            answer=result,
            usage=usage or {},
            model=model,
            provider_payload=sent_payload,
            raw_answer=str(raw_answer),
            retry_used=retry_used,
        )

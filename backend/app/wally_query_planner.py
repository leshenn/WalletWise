from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from .ai_service import AIConfigurationError, AIProviderError
from .wally_query_plan import QueryPlan


class PlannerEntities(BaseModel):
    subject: str | None = None
    targetAmount: float | None = Field(default=None, ge=0)
    timeframeMonths: int | None = Field(default=None, ge=1)
    existingSavings: float | None = Field(default=None, ge=0)
    periodicContribution: float | None = Field(default=None, ge=0)
    purchasePrice: float | None = Field(default=None, ge=0)
    recurringPayment: float | None = Field(default=None, ge=0)
    paymentFrequency: str | None = None
    category: str | None = None
    merchant: str | None = None
    periodCount: int | None = Field(default=None, ge=2, le=12)


class PlannerPayload(BaseModel):
    route: str
    queryType: str
    measure: str
    operation: str
    groupBy: str = "none"
    scope: str = "none"
    period: str | None = None
    direction: str | None = None
    limit: int | None = Field(default=None, ge=1, le=10)
    breakdownByPeriod: bool = False
    entities: PlannerEntities = Field(default_factory=PlannerEntities)
    aiIntent: str | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


@dataclass(frozen=True)
class AIQueryPlanResult:
    plan: QueryPlan
    model: str
    usage: dict[str, Any]
    raw_response: str
    provider_payload: dict[str, Any]
    request_body: dict[str, Any]


class WallyAIQueryPlanner:
    """Tiny language-only semantic planner.

    It receives only the user's wording plus compact goal state when the current
    message is a genuine follow-up. It never receives transactions, balances,
    raw descriptions, reports or statement content.
    """

    DEFAULT_URL = "https://api.groq.com/openai/v1/chat/completions"
    DEFAULT_MAX_TOKENS = 320
    PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "WALLY_QUERY_PLANNER_PROMPT.md"

    @classmethod
    @lru_cache(maxsize=1)
    def _system_prompt(cls) -> str:
        try:
            value = cls.PROMPT_FILE.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise AIConfigurationError(f"Wally query-planner prompt could not be loaded: {cls.PROMPT_FILE}") from exc
        if not value:
            raise AIConfigurationError("Wally query-planner prompt is empty.")
        return value

    @classmethod
    def enabled(cls) -> bool:
        value = (os.getenv("WALLETWISE_ROUTER_AI_ENABLED") or "true").strip().casefold()
        return value in {"1", "true", "yes", "on"}

    @classmethod
    def confidence_threshold(cls) -> float:
        raw = (os.getenv("WALLETWISE_ROUTER_CONFIDENCE_THRESHOLD") or "0.78").strip()
        try:
            return max(0.50, min(float(raw), 0.98))
        except ValueError:
            return 0.78

    @classmethod
    def _max_tokens(cls) -> int:
        raw = (os.getenv("WALLETWISE_ROUTER_MAX_TOKENS") or str(cls.DEFAULT_MAX_TOKENS)).strip()
        try:
            return max(180, min(int(raw), 600))
        except ValueError:
            return cls.DEFAULT_MAX_TOKENS

    @classmethod
    def _model(cls) -> str:
        return (os.getenv("GROQ_ROUTER_MODEL") or os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b").strip()

    @staticmethod
    def approx_tokens(text: str) -> int:
        return max(1, math.ceil(len(text or "") / 4))

    @staticmethod
    def compact_memory(memory_state: dict[str, Any] | None) -> dict[str, Any]:
        memory_state = memory_state or {}
        allowed = {
            "subject", "targetAmount", "amount", "timeframeMonths", "existingSavings",
            "periodicContribution", "purchasePrice", "recurringPayment", "paymentFrequency", "intent",
            "category", "merchant", "period", "previousPeriod", "groupBy", "measure", "operation",
        }
        return {key: memory_state.get(key) for key in allowed if memory_state.get(key) is not None}

    @classmethod
    def plan(
        cls,
        *,
        question: str,
        memory_state: dict[str, Any] | None = None,
    ) -> AIQueryPlanResult:
        if not cls.enabled():
            raise AIConfigurationError("Wally AI query planning is disabled.")
        api_key = (os.getenv("GROQ_API_KEY") or "").strip()
        if not api_key:
            raise AIConfigurationError("Groq is not configured for Wally's semantic query planner.")

        model = cls._model()
        api_url = (os.getenv("GROQ_API_URL") or cls.DEFAULT_URL).strip()
        request_body = {
            "q": question.strip(),
            "memory": cls.compact_memory(memory_state),
        }
        user_text = json.dumps(request_body, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": cls._system_prompt()},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0,
            "reasoning_effort": "low",
            "max_completion_tokens": cls._max_tokens(),
            "response_format": {"type": "json_object"},
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    api_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.RequestError as exc:
            raise AIProviderError(f"Could not reach Groq query planner: {exc}") from exc

        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = body.get("error", {}).get("message") or body.get("message") or ""
            except Exception:
                detail = response.text[:400]
            raise AIProviderError(f"Groq query planner failed ({response.status_code}). {detail}".strip())

        try:
            body = response.json()
            raw = str(body["choices"][0]["message"]["content"])
            parsed = json.loads(raw)
            validated = PlannerPayload.model_validate(parsed)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise AIProviderError("Wally's semantic query planner returned invalid JSON.") from exc

        allowed_routes = {"local", "ai", "clarify"}
        allowed_query_types = {"aggregate", "transaction", "comparison", "calculation", "advice", "explanation", "factual", "unknown"}
        allowed_measures = {"spending", "income", "savings", "balance", "transactions", "goal", "affordability", "budget", "general"}
        allowed_group = {"none", "category", "merchant", "period"}
        allowed_scope = {"none", "all_history", "past_complete_periods", "specific_period", "latest_complete_period", "previous_complete_period", "latest_period", "current_vs_previous"}
        if validated.route not in allowed_routes:
            raise AIProviderError(f"Unknown planner route: {validated.route}")
        if validated.queryType not in allowed_query_types:
            raise AIProviderError(f"Unknown planner query type: {validated.queryType}")
        if validated.measure not in allowed_measures:
            raise AIProviderError(f"Unknown planner measure: {validated.measure}")
        if validated.groupBy not in allowed_group:
            raise AIProviderError(f"Unknown planner grouping: {validated.groupBy}")
        if validated.scope not in allowed_scope:
            raise AIProviderError(f"Unknown planner scope: {validated.scope}")

        plan = QueryPlan(
            route=validated.route,
            query_type=validated.queryType,
            measure=validated.measure,
            operation=validated.operation,
            group_by=validated.groupBy,
            scope=validated.scope,
            period=validated.period,
            direction=validated.direction,
            limit=validated.limit,
            breakdown_by_period=validated.breakdownByPeriod,
            entities=validated.entities.model_dump(exclude_none=True),
            ai_intent=validated.aiIntent,
            confidence=float(validated.confidence),
            reason=validated.reason or "AI semantic query planner interpreted ambiguous wording.",
            source="ai_query_planner",
        )
        usage = body.get("usage") if isinstance(body, dict) else {}
        return AIQueryPlanResult(
            plan=plan,
            model=model,
            usage=usage or {},
            raw_response=raw,
            provider_payload=payload,
            request_body=request_body,
        )

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Sentiment = Literal["positive", "negative", "neutral"]


class AIKeyMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=120)
    change: str | None = Field(default=None, max_length=160)
    sentiment: Sentiment = "neutral"


class AIInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=500)
    sentiment: Sentiment = "neutral"


class AIRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=500)
    impact: str | None = Field(default=None, max_length=180)


class WalletWiseAIResponse(BaseModel):
    """Validated response contract between the model and WalletWise UI."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=800)
    status: str = Field(min_length=1, max_length=80)
    key_metrics: list[AIKeyMetric] = Field(default_factory=list, max_length=4)
    insights: list[AIInsight] = Field(default_factory=list, max_length=3)
    recommendations: list[AIRecommendation] = Field(default_factory=list, max_length=3)
    warnings: list[str] = Field(default_factory=list, max_length=2)
    next_step: str | None = Field(default=None, max_length=500)
    needs_user_input: bool = False
    follow_up_question: str | None = Field(default=None, max_length=300)


# A deliberately simple schema is sent to Groq. Keeping it flat avoids
# provider-specific JSON Schema edge cases while still guaranteeing the UI shape.
WALLETWISE_RESPONSE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "status": {"type": "string"},
        "key_metrics": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "change": {"type": ["string", "null"]},
                    "sentiment": {
                        "type": "string",
                        "enum": ["positive", "negative", "neutral"],
                    },
                },
                "required": ["label", "value", "change", "sentiment"],
            },
        },
        "insights": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "sentiment": {
                        "type": "string",
                        "enum": ["positive", "negative", "neutral"],
                    },
                },
                "required": ["title", "text", "sentiment"],
            },
        },
        "recommendations": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "impact": {"type": ["string", "null"]},
                },
                "required": ["title", "text", "impact"],
            },
        },
        "warnings": {
            "type": "array",
            "maxItems": 2,
            "items": {"type": "string"},
        },
        "next_step": {"type": ["string", "null"]},
        "needs_user_input": {"type": "boolean"},
        "follow_up_question": {"type": ["string", "null"]},
    },
    "required": [
        "title",
        "summary",
        "status",
        "key_metrics",
        "insights",
        "recommendations",
        "warnings",
        "next_step",
        "needs_user_input",
        "follow_up_question",
    ],
}

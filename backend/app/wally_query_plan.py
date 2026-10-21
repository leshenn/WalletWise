from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from .wally_router import SmartLocalRouter


@dataclass(frozen=True)
class QueryPlan:
    """A semantic description of what Wally believes the user is asking.

    The plan is deliberately independent from presentation. Python executes local
    plans deterministically. AI plans only tell the context builder which small
    slice of financial data is relevant.
    """

    route: str  # local | ai | classify | clarify
    query_type: str  # aggregate | transaction | comparison | calculation | advice | explanation | factual | unknown
    measure: str  # spending | income | savings | balance | transactions | goal | affordability | budget | general
    operation: str  # sum | rank | max_transaction | compare | count | latest | required_contribution | ...
    group_by: str = "none"  # none | category | merchant | period
    scope: str = "none"  # all_history | past_complete_periods | specific_period | latest_complete_period | current_vs_previous | none
    period: str | None = None
    direction: str | None = None
    limit: int | None = None
    breakdown_by_period: bool = False
    entities: dict[str, Any] = field(default_factory=dict)
    ai_intent: str | None = None
    confidence: float = 0.0
    reason: str = ""
    source: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "queryType": self.query_type,
            "measure": self.measure,
            "operation": self.operation,
            "groupBy": self.group_by,
            "scope": self.scope,
            "period": self.period,
            "direction": self.direction,
            "limit": self.limit,
            "breakdownByPeriod": self.breakdown_by_period,
            "entities": self.entities,
            "aiIntent": self.ai_intent,
            "confidence": round(float(self.confidence), 3),
            "reason": self.reason,
            "source": self.source,
        }


@dataclass(frozen=True)
class PlanValidation:
    valid: bool
    issues: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "checks": list(self.checks), "issues": list(self.issues)}


class DeterministicQueryPlanner:
    """Build a semantic plan when the current question is unambiguous.

    Important: explicit nouns and scope in the *current* message outrank generic
    phrases. This is what prevents "biggest expense category" from becoming
    "largest individual transaction".
    """

    RANK_WORDS = ("biggest", "largest", "highest", "most", "top")
    # Category aliases describe what to filter, NOT an intent on their own.
    # A bank fee is not interchangeable with a user's overall spending.
    CATEGORY_ALIASES = (
        (r"\b(?:bank\s+(?:fees?|charges?)|banking\s+fees?|account\s+fees?|transaction\s+fees?|service\s+fees?)\b", "Bank Fees"),
        (r"\b(?:groceries|grocery(?:\s+(?:shopping|expenses?|spending|purchases?|costs?))?)\b", "Groceries"),
        (r"\b(?:fuel|petrol|diesel)\b", "Fuel"),
        (r"\b(?:transport|commuting)\b", "Transport"),
        (r"\b(?:takeaways?|eating\s+out|restaurants?)\b", "Eating Out"),
        (r"\bsubscriptions?\b", "Subscriptions"),
        (r"\b(?:entertainment)\b", "Entertainment"),
        (r"\b(?:shopping)\b", "Shopping"),
        (r"\b(?:insurance)\b", "Insurance"),
    )
    TREND_MARKERS = (
        "increas", "decreas", "going up", "gone up", "going down", "gone down",
        "rising", "risen", "falling", "fallen", "trending", "trend", "getting higher",
        "getting lower", "more expensive", "less expensive", "changed over", "changing over",
    )

    @classmethod
    def explicit_category(cls, text: str) -> str | None:
        for pattern, category in cls.CATEGORY_ALIASES:
            if re.search(pattern, text):
                return category
        return None
    HISTORY_WORDS = (
        "past months", "previous months", "last few months", "recent months",
        "over the months", "across the months", "past periods", "previous periods",
        "over time", "historically", "history", "all time", "entire history",
    )

    @classmethod
    def build(cls, question: str, conversation_summary: dict[str, Any] | None = None) -> QueryPlan:
        text = SmartLocalRouter.normalize(question)
        followup = SmartLocalRouter.is_contextual_followup(question)
        memory_state = {}
        if followup and isinstance(conversation_summary, dict):
            state = conversation_summary.get("state")
            memory_state = state if isinstance(state, dict) else {}

        entities = SmartLocalRouter.extract_goal_entities(question, memory=memory_state)
        entities.update(SmartLocalRouter.extract_purchase_entities(question))
        period = cls._extract_period_text(text)
        if period:
            entities.setdefault("period", period)

        scope = cls._scope(text, period)
        named_category = cls.explicit_category(text)
        if named_category:
            entities["category"] = named_category
        rank = any(re.search(rf"\b{re.escape(word)}\b", text) for word in cls.RANK_WORDS)

        # A short "what about June?" keeps the preceding metric/category and
        # changes ONLY the named month. Never assume a follow-up from an old
        # conversation when a complete standalone question was supplied.
        if (followup and period and not cls.explicit_category(text)
                and re.search(r"\b(?:what about|how about)\b", text)
                and memory_state.get("operation") == "sum"
                and memory_state.get("measure") in {"spending", "income", "savings"}):
            prior_category = str(memory_state.get("category") or "")
            prior_group = "category" if prior_category and memory_state.get("groupBy") == "category" else "none"
            if prior_category and prior_group == "category":
                entities["category"] = prior_category
            return QueryPlan(
                route="local", query_type="aggregate", measure=str(memory_state["measure"]),
                operation="sum", group_by=prior_group, scope="specific_period", period=period,
                entities=entities, confidence=0.99,
                reason="Follow-up changes the month while inheriting the previous financial measure and category.",
            )

        # Unsupported filters, comparisons and reasoning must never be silently
        # reduced to a simpler query just because a familiar keyword was found.
        # The AI route receives Python-generated evidence, not raw statement PDFs.
        complex_reason = cls._complex_query_reason(text)
        if complex_reason:
            ai_intent = cls._complex_ai_intent(text)
            return QueryPlan(
                route="ai", query_type="explanation", measure=cls._measure_for_ai_intent(ai_intent),
                operation="interpret", scope=scope, period=period, entities=entities,
                ai_intent=ai_intent, confidence=0.99, reason=complex_reason,
                source="complexity_guard",
            )

        # A trend is a time-series calculation, not a generic two-period
        # spending comparison. Preserve both the named category and timeframe.
        # Explicitly mentioned N months takes precedence over the 3-month default.
        category = cls.explicit_category(text)
        trending = any(marker in text for marker in cls.TREND_MARKERS)
        trend_subject = bool(category) or cls._looks_like_spending(text)
        if trending and trend_subject:
            explicit_count = re.search(r"\b(?:last|past|previous|over(?:\s+the)?\s+last)\s+(\d{1,2})\s+months?\b", text)
            months = min(max(int(explicit_count.group(1)), 2), 12) if explicit_count else 3
            if not period and scope in {"none", "latest_complete_period"}:
                scope = "past_complete_periods"
            if category:
                entities["category"] = category
            entities["periodCount"] = months
            return QueryPlan(
                route="local", query_type="comparison", measure="spending", operation="compare",
                group_by="category" if category else "period", scope=scope,
                period=period,
                direction="increase" if any(x in text for x in ("increas", "going up", "gone up", "rising", "risen", "higher")) else
                          "decrease" if any(x in text for x in ("decreas", "going down", "gone down", "falling", "fallen", "lower")) else None,
                entities=entities, confidence=0.995,
                reason="Time-series trend requested; preserve the named spending subject and analyse month-by-month totals.",
            )

        # Explicit category/merchant grouping always wins over generic "expense".
        if "category" in text and rank and cls._looks_like_spending(text):
            historical = scope in {"past_complete_periods", "all_history"}
            return QueryPlan(
                route="local", query_type="aggregate", measure="spending", operation="rank",
                group_by="category", scope=scope if scope != "none" else "all_history",
                period=period, direction="highest", limit=5 if historical else 3,
                breakdown_by_period=historical,
                entities=entities, confidence=0.995,
                reason="The current question explicitly asks to rank spending by category; category grouping outranks the generic phrase 'biggest expense'.",
            )

        if "merchant" in text and rank and cls._looks_like_spending(text):
            requested_limit = next((v for word, v in {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.items()
                                    if re.search(rf"\b{word}\b", text)), 5)
            numeric_limit = re.search(r"\b(?:top\s+)?([1-5])\s+(?:merchants?|shops?|stores?)\b", text)
            requested_limit = int(numeric_limit.group(1)) if numeric_limit else requested_limit
            return QueryPlan(
                route="local", query_type="aggregate", measure="spending", operation="rank",
                group_by="merchant", scope=scope if scope != "none" else "all_history",
                period=period, direction="highest", limit=requested_limit, entities=entities,
                confidence=0.99, reason="The user explicitly asks for highest spending grouped by merchant.",
            )

        # "Where did my money go?" is category composition, not a transaction.
        if any(p in text for p in ("where is my money going", "where did my money go", "where has my money gone")):
            return QueryPlan(
                route="local", query_type="aggregate", measure="spending", operation="rank",
                group_by="category", scope=scope if scope != "none" else "all_history",
                period=period, direction="highest", limit=5, breakdown_by_period=False,
                entities=entities, confidence=0.97, reason="The question asks for spending composition by category.",
            )

        # Single named category sums should not be interpreted as total spending
        # or sent through a generic AI context by accident.
        if category and re.search(r"\b(?:how much|total|cost|paid|fees?|charges?|spent|spending)\b", text) and not rank:
            return QueryPlan(
                route="local", query_type="aggregate", measure="spending", operation="sum",
                group_by="category", scope=scope if scope != "none" else "all_history",
                period=period, entities={**entities, "category": category}, confidence=0.99,
                reason=f"Calculate the explicitly named {category} category, not total spending.",
            )

        if re.search(r"\b(?:smallest|least expensive|lowest|cheapest)\b.*\b(?:expense|purchase|transaction)\b", text):
            return QueryPlan(
                route="local", query_type="transaction", measure="spending", operation="min_transaction",
                group_by="none", scope=scope if scope != "none" else "all_history",
                period=period, direction="lowest", limit=1, entities=entities, confidence=0.99,
                reason="Find the single smallest debit expense in the requested timeframe.",
            )

        # A plain biggest expense asks for one transaction, including wordings
        # such as "biggest individual expense" and "most expensive transaction".
        if any(p in text for p in ("largest expense", "biggest expense", "largest purchase", "biggest purchase", "most expensive purchase")) or re.search(r"\b(?:biggest|largest|most expensive)\s+(?:individual\s+)?(?:expense|transaction|purchase)\b", text):
            return QueryPlan(
                route="local", query_type="transaction", measure="spending", operation="max_transaction",
                group_by="none", scope=scope if scope != "none" else "all_history",
                period=period, direction="highest", limit=1, entities=entities,
                confidence=0.98, reason="The question asks for the single largest expense transaction and does not request grouping.",
            )

        # Let the mature goal parser handle deterministic arithmetic and obvious advice.
        routed = SmartLocalRouter.route(question, conversation_summary if followup else {})
        merged_entities = dict(entities)
        merged_entities.update(routed.entities or {})

        if routed.intent in {"saving_goal_calculation", "saving_goal_missing_info"}:
            operation = str(merged_entities.get("calculation") or "required_contribution")
            return QueryPlan(
                route="local" if routed.intent == "saving_goal_calculation" else "classify",
                query_type="calculation", measure="goal", operation=operation,
                scope="none", entities=merged_entities, ai_intent="saving_goal",
                confidence=routed.confidence, reason=routed.reason, source=routed.source,
            )

        if routed.mode == "ai":
            return QueryPlan(
                route="ai", query_type="advice", measure=cls._measure_for_ai_intent(routed.intent),
                operation="interpret", scope=scope, period=period,
                entities=merged_entities, ai_intent=routed.intent,
                confidence=routed.confidence, reason=routed.reason, source=routed.source,
            )

        # Deterministic factual routes mapped into semantic plans.
        if routed.mode == "local":
            mapped = cls._map_local_intent(routed.intent, scope, period, merged_entities, routed.confidence, routed.reason)
            if mapped:
                return mapped

        return QueryPlan(
            route="classify", query_type="unknown", measure="general", operation="understand",
            scope=scope, period=period, entities=merged_entities,
            confidence=0.40, reason="The deterministic planner cannot safely determine the requested calculation.",
        )

    @classmethod
    def _map_local_intent(
        cls, intent: str, scope: str, period: str | None, entities: dict[str, Any], confidence: float, reason: str
    ) -> QueryPlan | None:
        scope = scope if scope != "none" else ("specific_period" if period else "latest_complete_period")
        mapping = {
            "balance": ("factual", "balance", "latest", "none"),
            "transaction_count": ("aggregate", "transactions", "count", "none"),
            "spending_total": ("aggregate", "spending", "sum", "none"),
            "category_spending": ("aggregate", "spending", "sum", "category"),
            "merchant_spending": ("aggregate", "spending", "sum", "merchant"),
            "income_total": ("aggregate", "income", "sum", "none"),
            "savings_total": ("aggregate", "savings", "sum", "none"),
            "largest_expense": ("transaction", "spending", "max_transaction", "none"),
            "spending_comparison": ("comparison", "spending", "compare", "period"),
            "top_categories": ("aggregate", "spending", "rank", "category"),
            "top_category": ("aggregate", "spending", "rank", "category"),
            "top_merchant": ("aggregate", "spending", "rank", "merchant"),
        }
        values = mapping.get(intent)
        if not values:
            return None
        query_type, measure, operation, group_by = values
        if operation == "compare":
            scope = "current_vs_previous"
        if operation == "rank" and period is None and scope == "latest_complete_period":
            # "my biggest spend category" without a named period naturally means
            # imported history rather than whichever month happens to be latest.
            scope = "all_history"
        return QueryPlan(
            route="local", query_type=query_type, measure=measure, operation=operation,
            group_by=group_by, scope=scope, period=period,
            direction="highest" if operation in {"rank", "max_transaction"} else None,
            limit=5 if operation == "rank" else (1 if operation == "max_transaction" else None),
            entities=entities, confidence=confidence, reason=reason,
        )

    @classmethod
    def _complex_query_reason(cls, text: str) -> str | None:
        """Conservative capability gate: avoid wrong *local* answers for multi-step asks.

        This does not mean a language model may invent figures. AIContextBuilder
        supplies auditable precomputed aggregates for the reasoning path.
        """
        # Month-window totals and per-month statistical questions are not
        # supported by the existing single-period sum handler.
        trending = any(marker in text for marker in cls.TREND_MARKERS)
        if (re.search(r"\b(?:last|past|previous)\s+(?:\d+|two|three|four|five|six|several|few)\s+months?\b", text)
                and not trending and not ("category" in text and any(w in text for w in cls.RANK_WORDS))):
            return "A total across multiple named months cannot use the single-period executor."
        if "categor" in text and trending and any(w in text for w in ("which", "contributed", "individual", "faster", "fastest")):
            return "This question requires comparison of multiple category trends."
        if re.search(r"\b(?:faster|slower) than\b|\bmore on\b.*\bor\b|\bless on\b.*\bor\b", text):
            return "This compares two different financial subjects."
        if ("salary" in text or "income" in text) and ("left" in text or "after spending" in text or "after expenses" in text):
            return "Remaining-income calculation needs income and expenditure together."
        if re.search(r"\bwhich months?\b|\bmost expensive months?\b|\bhighest.spending months?\b", text):
            return "Ranking months is different from finding an individual expense."
        if re.search(r"\b(?:consistent|consistency|unpredictable|volatile|variable|regularly|one.off|responsible|financial position|both)\b", text):
            return "This requires interpretation across several metrics or periods."
        patterns = (
            (r"\b(?:exclud(?:e|ing)|except|without|ignore|subtract|deduct|remove)\b", "A financial exclusion/filter needs explicit verification."),
            (r"\b(?:percentage|percent|proportion|share|ratio|median|average|mean|frequency|frequently|distribution|distributed)\b", "The requested statistical calculation is not implemented by the simple executor."),
            (r"\b(?:if i|what if|would have|could have|forecast|predict|project|anomal(?:y|ies)|unusual|outlier|duplicate|recurring)\b", "This question needs multi-step analysis or a hypothetical calculation."),
            (r"\b(?:why|reason|explain|advis(?:e|e)|recommend|improv(?:e|ing)|afford|realistic|better decision|worth it|help me)\b", "The user requests reasoning or advice rather than only an account fact."),
            (r"\b(?:but|although|even though|whereas|while)\b", "This asks about multiple related conditions that a single local aggregate cannot answer."),
            (r"\b(?:compared? to|compared? with|versus|vs\.?|difference between|between [a-z]+ (?:and|&|or) [a-z]+)\b", "The question requests a comparison not safely represented by the two-period default."),
            (r"\b(?:per transaction|per day|weekday|weekend|most often|how often|number of times)\b", "The requested grouping or transaction frequency is unsupported by this local route."),
        )
        for regex, reason in patterns:
            if re.search(regex, text):
                return reason
        # Explicit multiple named months cannot be treated as a single-period query.
        names = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
        found = {name[:3] for name in names if re.search(rf"\b{re.escape(name)}\b", text)}
        if len(found) > 1:
            return "Several explicitly named months require a true multi-period comparison."
        # Multiple separately named categories require a multi-subject calculation.
        matches = {category for pattern, category in cls.CATEGORY_ALIASES if re.search(pattern, text)}
        if len(matches) > 1:
            return "Several spending categories require a multi-subject comparison."
        return None

    @staticmethod
    def _complex_ai_intent(text: str) -> str:
        if "budget" in text:
            return "budget_analysis"
        if any(t in text for t in ("afford", "can i buy", "can i get", "should i buy")):
            return "affordability"
        if any(t in text for t in ("save for", "save up", "saving goal")):
            return "saving_goal"
        if "savings" in text or "saving" in text:
            return "savings_analysis"
        if any(t in text for t in ("spend", "spent", "expense", "fees", "charge", "transaction", "merchant", "grocer", "fuel")):
            return "spending_analysis"
        return "general_finance"

    @classmethod
    def _scope(cls, text: str, period: str | None) -> str:
        if period:
            return "specific_period"
        if any(p in text for p in ("all time", "entire history", "total ever", "overall history")):
            return "all_history"
        if any(p in text for p in cls.HISTORY_WORDS):
            return "past_complete_periods"
        if re.search(r"\btotal\b", text) and not re.search(r"\b(?:last|past|previous|this|current|latest)\b", text):
            return "all_history"
        if any(p in text for p in ("last month", "last complete month", "last complete period")):
            return "latest_complete_period"
        if any(p in text for p in ("previous month", "previous period", "month before")):
            return "previous_complete_period"
        if any(p in text for p in ("this month", "current month", "latest month", "current period")):
            return "latest_period"
        return "none"

    @classmethod
    def _extract_period_text(cls, text: str) -> str | None:
        months = (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
            "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
        )
        for month in months:
            match = re.search(rf"\b{month}\b(?:\s+(20\d{{2}}))?", text)
            if match:
                return " ".join(part for part in (month, match.group(1)) if part)
        return None

    @staticmethod
    def _looks_like_spending(text: str) -> bool:
        return any(word in text for word in ("expense", "spend", "spent", "spending", "purchase", "money"))

    @staticmethod
    def _measure_for_ai_intent(intent: str) -> str:
        return {
            "affordability": "affordability",
            "saving_goal": "goal",
            "spending_analysis": "spending",
            "category_analysis": "spending",
            "savings_analysis": "savings",
            "budget_analysis": "budget",
            "general_finance": "general",
        }.get(intent, "general")


class QueryPlanValidator:
    """Reject semantically inconsistent local plans before they can answer."""

    LOCAL_OPERATIONS = {
        "sum", "rank", "max_transaction", "min_transaction", "compare", "count", "latest",
        "required_contribution", "time_to_target", "future_value",
    }

    @classmethod
    def validate(cls, question: str, plan: QueryPlan) -> PlanValidation:
        text = SmartLocalRouter.normalize(question)
        checks: list[str] = []
        issues: list[str] = []

        checks.append("Current question is the primary source of intent; memory may only fill missing follow-up fields.")

        if plan.route == "local":
            if plan.operation == "max_transaction" and re.search(r"\b(?:smallest|lowest|least expensive|cheapest)\b", text):
                issues.append("The question requests the smallest expense but the proposed local operation selects the largest.")
            if plan.operation not in cls.LOCAL_OPERATIONS:
                issues.append(f"Local operation '{plan.operation}' is not supported.")
            else:
                checks.append(f"Local operation '{plan.operation}' is supported.")

            # Subject-preservation check: a bank-fee comparison must NEVER be
            # answered as a comparison of everyone's total spending.
            explicit_category = DeterministicQueryPlanner.explicit_category(text)
            if explicit_category and plan.operation == "sum":
                if plan.group_by != "category" or str(plan.entities.get("category") or "").casefold() != explicit_category.casefold():
                    issues.append(f"Sum explicitly asks about {explicit_category}, but the plan does not preserve that filter.")
            if explicit_category and plan.operation == "compare":
                if plan.group_by != "category" or str(plan.entities.get("category") or "").casefold() != explicit_category.casefold():
                    issues.append(f"Comparison explicitly asks about {explicit_category}, but the plan does not preserve that category filter.")
                else:
                    checks.append(f"Category-specific comparison is restricted to {explicit_category}.")
            if "category" in text:
                if plan.group_by != "category" and plan.operation in {"rank", "max_transaction"}:
                    issues.append("Question explicitly asks about a category, but the plan is not grouped by category.")
                else:
                    checks.append("Explicit category wording is respected by the plan.")

            if "merchant" in text and plan.operation == "rank" and plan.group_by != "merchant":
                issues.append("Question explicitly asks about merchants, but the plan is not grouped by merchant.")

            if any(p in text for p in DeterministicQueryPlanner.HISTORY_WORDS):
                if plan.scope not in {"past_complete_periods", "all_history"}:
                    issues.append("Question asks for historical/multi-period scope, but the plan resolves to a single/default period.")
                else:
                    checks.append("Historical scope is preserved.")

            if plan.query_type == "transaction" and plan.group_by != "none":
                issues.append("A single-transaction query cannot also group results.")

            if plan.operation == "rank" and plan.group_by not in {"category", "merchant"}:
                issues.append("Rank aggregation requires category or merchant grouping.")

            if plan.measure == "goal":
                entities = plan.entities or {}
                if plan.operation == "required_contribution":
                    missing = [name for name in ("targetAmount", "timeframeMonths") if not entities.get(name)]
                    if missing:
                        issues.append("Goal contribution calculation is missing: " + ", ".join(missing))
                elif plan.operation == "time_to_target":
                    missing = [name for name in ("targetAmount", "periodicContribution") if not entities.get(name)]
                    if missing:
                        issues.append("Time-to-target calculation is missing: " + ", ".join(missing))

        if plan.route == "ai" and not plan.ai_intent:
            issues.append("AI plan is missing an ai_intent for the context builder.")
        elif plan.route == "ai":
            checks.append(f"AI context will use intent '{plan.ai_intent}'.")

        if plan.route == "classify":
            checks.append("Ambiguous wording will be sent to the tiny language-only query planner; no financial data is sent at this stage.")

        return PlanValidation(valid=not issues, issues=tuple(issues), checks=tuple(checks))

    @classmethod
    def validate_answer(cls, plan: QueryPlan, data_used: dict[str, Any]) -> PlanValidation:
        issues: list[str] = []
        checks: list[str] = []
        fact = str(data_used.get("localFactType") or "")

        expected: set[str] | None = None
        if plan.operation == "rank" and plan.group_by == "category":
            expected = {"category_ranking", "top_category", "top_categories"}
        elif plan.operation == "rank" and plan.group_by == "merchant":
            expected = {"merchant_ranking", "top_merchant"}
        elif plan.operation == "max_transaction":
            expected = {"largest_expense"}
        elif plan.operation == "min_transaction":
            expected = {"smallest_expense"}
        elif plan.operation == "count":
            expected = {"transaction_count"}
        elif plan.operation == "latest" and plan.measure == "balance":
            expected = {"balance"}
        elif plan.operation == "compare":
            expected = {"category_trend"} if plan.entities.get("periodCount") or plan.group_by == "category" else {"spending_comparison"}
        elif plan.operation == "sum" and plan.measure == "spending":
            expected = {"spending_total", "category_or_merchant_spend"}
        elif plan.operation == "sum" and plan.measure == "income":
            expected = {"income_total"}
        elif plan.operation == "sum" and plan.measure == "savings":
            expected = {"savings_total"}
        elif plan.measure == "goal":
            expected = {"saving_goal"}

        if expected is not None:
            if fact not in expected:
                issues.append(f"Local answer type '{fact}' does not match plan expectation {sorted(expected)}.")
            else:
                checks.append(f"Answer type '{fact}' matches the semantic plan.")

        if plan.scope == "past_complete_periods" and plan.operation == "rank":
            if int(data_used.get("periodsIncluded") or 0) < 1:
                issues.append("Historical ranking returned no resolved periods.")
            else:
                checks.append("Historical ranking reports how many complete periods were used.")

        return PlanValidation(valid=not issues, issues=tuple(issues), checks=tuple(checks))


def with_ai_fallback(plan: QueryPlan, reason: str) -> QueryPlan:
    return replace(
        plan,
        route="ai",
        query_type="explanation",
        measure="general" if plan.measure == "general" else plan.measure,
        operation="interpret",
        ai_intent=plan.ai_intent or _default_ai_intent(plan),
        reason=reason,
        source="plan_validation_fallback",
    )


def _default_ai_intent(plan: QueryPlan) -> str:
    if plan.measure == "affordability":
        return "affordability"
    if plan.measure == "goal":
        return "saving_goal"
    if plan.measure == "spending":
        return "spending_analysis"
    if plan.measure == "savings":
        return "savings_analysis"
    if plan.measure == "budget":
        return "budget_analysis"
    return "general_finance"

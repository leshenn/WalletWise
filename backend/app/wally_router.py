from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RouteDecision:
    intent: str
    mode: str  # local | ai | classify
    confidence: float
    reason: str
    entities: dict[str, Any] = field(default_factory=dict)
    source: str = "rules"


class SmartLocalRouter:
    """Conservative local intent router for Wally.

    The router has one guiding rule: a cheap local answer is only useful when it
    is also the *right* answer. High-confidence deterministic questions stay in
    Python; ambiguity falls through to the LLM instead of guessing from one
    keyword such as "save".
    """

    GOAL_SUBJECTS = (
        "car", "vehicle", "phone", "iphone", "laptop", "computer", "house",
        "home", "holiday", "vacation", "wedding", "deposit", "emergency fund",
        "bike", "motorbike", "motorcycle", "appliance", "tv",
    )

    AFFORDABILITY_MARKERS = (
        "can i afford", "could i afford", "should i buy", "is it affordable",
        "affordable for me", "comfortably afford", "safe to buy", "would buying",
        "would it be a bad idea to buy", "bad idea financially", "worth buying",
        "good financial decision", "financial decision", "financially sensible",
        "financially responsible", "make financial sense", "does it make sense financially",
        "financial pressure", "under financial pressure", "put me under pressure",
        "stretch my finances", "stretch me", "strain my finances",
        "too expensive for me", "too much for me", "manageable for me", "without struggling",
        "hurt me financially", "okay for my finances", "okay financially",
        "take a contract", "sign a contract", "phone contract", "contract for a",
    )

    FOLLOW_UP_MARKERS = (
        "what if", "how about", "instead", "make it", "change it",
        "that amount", "that goal", "that budget", "that purchase",
        "same goal", "same period", "same thing", "still afford", "still save",
        "what about", "and if", "then what", "more per month", "less per month",
        "the previous", "earlier", "you said", "as before", "same as before",
    )

    ADVICE_MARKERS = (
        "why", "how can i", "how should i", "what should i", "recommend",
        "advice", "realistic", "realistically", "help me", "cut back",
        "reduce my", "improve my", "what can i do", "what do you think",
    )

    @staticmethod
    def normalize(text: str) -> str:
        value = " ".join((text or "").strip().split()).casefold()
        value = value.replace("’", "'")
        return value

    @classmethod
    def is_contextual_followup(cls, question: str) -> bool:
        """Return True only when the current message genuinely depends on prior chat state.

        Saved-chat memory is intentionally *not* a default input to routing. A complete
        new question must behave the same in an old chat as it does in a new chat.
        Memory may only fill blanks for a real follow-up.
        """
        text = cls.normalize(question)
        if not text:
            return False
        if any(marker in text for marker in cls.FOLLOW_UP_MARKERS):
            return True
        words = re.findall(r"[a-z0-9']+", text)
        if len(words) <= 8 and any(word in {"it", "that", "this", "those", "same"} for word in words):
            # Short deictic questions such as "Can I afford that?" are follow-ups.
            return True
        return False

    @classmethod
    def route(
        cls,
        question: str,
        conversation_summary: dict[str, Any] | None = None,
    ) -> RouteDecision:
        text = cls.normalize(question)
        use_memory = cls.is_contextual_followup(question)
        memory = cls._memory_state(conversation_summary or {}) if use_memory else {}
        entities = cls.extract_goal_entities(question, memory=memory)
        entities.update(cls.extract_purchase_entities(question))

        if not text:
            return RouteDecision("unknown", "ai", 0.0, "Empty question", entities)

        # Explicit affordability/judgement always benefits from reasoning.
        if any(marker in text for marker in cls.AFFORDABILITY_MARKERS):
            return RouteDecision(
                "affordability", "ai", 0.99,
                "The user is asking for an affordability judgement, not only arithmetic.",
                entities,
            )

        # Past-tense/history savings questions must win over goal detection.
        # This prevents "How much did I save last month?" from being mistaken
        # for "How much do I need to save for a goal?".
        if cls._is_savings_history(text):
            return RouteDecision(
                "savings_total", "local", 0.99,
                "The wording asks about historical/net savings, not a future target.",
                entities,
            )

        # A savings target can contain all the numbers needed for arithmetic but
        # still ask for judgement ("is this realistic?"). That is not a pure
        # calculator question and must go to the reasoning path.
        if ("save" in text or "saving" in text or "goal" in text) and any(
            marker in text
            for marker in (
                "realistic", "realistically", "achievable", "doable",
                "can i actually", "can i manage", "is this possible",
                "will i manage", "am i able to",
            )
        ):
            return RouteDecision(
                "saving_goal", "ai", 0.98,
                "The user is asking whether a savings goal is realistic, which requires financial judgement.",
                entities,
            )

        # Goal arithmetic takes priority over generic savings keyword matching.
        goal_signal = cls._goal_signal_score(text, entities)
        if goal_signal >= 3:
            action = entities.get("calculation")
            if action in {"required_contribution", "time_to_target", "future_value"}:
                return RouteDecision(
                    "saving_goal_calculation", "local", min(0.99, 0.72 + goal_signal * 0.04),
                    f"High-confidence savings-goal arithmetic ({action}).",
                    entities,
                )
            if entities.get("missing"):
                return RouteDecision(
                    "saving_goal_missing_info", "classify", 0.62,
                    "The goal is clear but local parsing is missing a value; let the tiny classifier check the wording before asking the user.",
                    entities,
                )

        # Explanatory/advisory wording should not be forced into a local template.
        if any(marker in text for marker in cls.ADVICE_MARKERS):
            return RouteDecision(
                cls._advice_intent(text), "ai", 0.94,
                "The user is asking for explanation, judgement or recommendations.",
                entities,
            )

        # High-confidence factual intents. These are intentionally phrase based,
        # with savings-history rules that require past/history context.
        factual = cls._factual_intent(text)
        if factual:
            return RouteDecision(
                factual, "local", 0.96,
                "High-confidence factual WalletWise question.",
                entities,
            )

        # A phrase such as "save 60000 in 3 years" can omit a question word.
        if goal_signal >= 2 and (entities.get("targetAmount") or entities.get("timeframeMonths")):
            if entities.get("calculation"):
                return RouteDecision(
                    "saving_goal_calculation", "local", 0.88,
                    "Savings target and timing imply deterministic goal arithmetic.",
                    entities,
                )
            return RouteDecision(
                "saving_goal_missing_info", "classify", 0.60,
                "A savings goal is evident but local parsing is incomplete; use the tiny classifier before asking for more information.",
                entities,
            )

        return RouteDecision(
            "unknown", "classify", 0.40,
            "Deterministic rules are not confident enough; use the tiny language classifier.",
            entities,
        )

    @classmethod
    def extract_goal_entities(
        cls,
        question: str,
        *,
        memory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        original = " ".join((question or "").strip().split())
        text = original.casefold()
        memory = memory or {}
        entities: dict[str, Any] = {}

        subject = cls._extract_subject(text)
        if not subject:
            subject = memory.get("subject")
        if subject:
            entities["subject"] = subject

        timeframe_months, duration_span = cls._extract_duration(text)
        if timeframe_months is None:
            timeframe_months = cls._as_positive_number(memory.get("timeframeMonths"))
            if timeframe_months is not None:
                timeframe_months = int(round(timeframe_months))
        if timeframe_months:
            entities["timeframeMonths"] = timeframe_months

        frequency = cls._requested_frequency(text)
        entities["requestedFrequency"] = frequency

        contribution, contribution_span = cls._extract_periodic_contribution(text)
        if contribution is not None:
            entities["periodicContribution"] = contribution
            entities["contributionFrequency"] = cls._contribution_frequency(text)

        existing, existing_span = cls._extract_existing_savings(text)
        if existing is not None:
            entities["existingSavings"] = existing

        excluded_spans = [span for span in (duration_span, contribution_span, existing_span) if span]
        money_candidates = cls._extract_money_candidates(original, excluded_spans)

        # Prefer explicit goal/cost amounts, then the largest sensible amount.
        target = cls._extract_target_amount(original)
        if target is None:
            candidates = [value for value, _span in money_candidates]
            if contribution is not None:
                candidates = [value for value in candidates if not math.isclose(value, contribution, rel_tol=0, abs_tol=0.01)]
            if existing is not None and len(candidates) > 1:
                removed = False
                filtered = []
                for value in candidates:
                    if not removed and math.isclose(value, existing, rel_tol=0, abs_tol=0.01):
                        removed = True
                        continue
                    filtered.append(value)
                candidates = filtered or candidates
            if candidates:
                target = max(candidates)

        if target is None:
            target = cls._as_positive_number(memory.get("targetAmount"))
        if target is None:
            # Compatibility with compact-memory versions that stored a single amount.
            target = cls._as_positive_number(memory.get("amount"))
        if target is not None and target >= 1:
            entities["targetAmount"] = round(target, 2)

        ask_how_long = bool(re.search(r"\b(?:how long|how many months|how many years|when will i|when would i)\b", text))
        ask_future_value = bool(
            re.search(r"\bhow much (?:will|would|could) i (?:have|save)\b", text)
            or re.search(r"\bwhat will i have\b", text)
        )
        ask_required = bool(
            re.search(r"\bhow much\b.*\b(?:need|must|should)\b.*\bsav", text)
            or re.search(r"\bhow much\b.*\bsav", text)
            or re.search(r"\bwhat do i need to save\b", text)
            or re.search(r"\b(?:monthly|per month|each month)\b.*\b(?:need|must|save)\b", text)
            or re.search(r"\bsav(?:e|ing)\b.*\b(?:in|over|within)\s+\d+\s*(?:months?|years?|yrs?)\b", text)
        )

        # "If I save R2,000 per month, how much will I have..." contains a
        # contribution amount, not a target amount. Do not persist it as a goal.
        if ask_future_value and contribution is not None and entities.get("targetAmount") is not None:
            if math.isclose(float(entities["targetAmount"]), float(contribution), rel_tol=0, abs_tol=0.01):
                entities.pop("targetAmount", None)
                entities.pop("remainingAmount", None)

        target_amount = entities.get("targetAmount")
        existing_savings = float(entities.get("existingSavings") or 0)
        remaining = max(0.0, float(target_amount or 0) - existing_savings) if target_amount is not None else None
        if remaining is not None:
            entities["remainingAmount"] = round(remaining, 2)

        if ask_how_long and target_amount is not None and contribution:
            entities["calculation"] = "time_to_target"
        elif ask_future_value and timeframe_months and contribution:
            entities["calculation"] = "future_value"
        elif target_amount is not None and timeframe_months and (ask_required or not contribution):
            entities["calculation"] = "required_contribution"
        elif target_amount is not None and timeframe_months:
            # If the wording is clearly about saving toward a target, this is still safe arithmetic.
            entities["calculation"] = "required_contribution"

        if cls._looks_like_goal(text):
            missing: list[str] = []
            if ask_how_long:
                if target_amount is None:
                    missing.append("target_amount")
                if not contribution:
                    missing.append("periodic_contribution")
            elif ask_future_value:
                if not timeframe_months:
                    missing.append("timeframe")
                if not contribution:
                    missing.append("periodic_contribution")
            else:
                if target_amount is None:
                    missing.append("target_amount")
                if not timeframe_months:
                    missing.append("timeframe")
            if missing:
                entities["missing"] = missing

        return entities

    @classmethod
    def extract_purchase_entities(cls, question: str) -> dict[str, Any]:
        """Extract purchase/commitment facts without assuming they are savings goals.

        A value next to "contract", "instalment" or "per month" is a recurring
        commitment, not a target amount. This distinction is especially important for
        affordability questions.
        """
        original = " ".join((question or "").strip().split())
        text = original.casefold()
        result: dict[str, Any] = {}
        subject = cls._extract_subject(text)
        if subject:
            result["subject"] = subject

        recurring_patterns = (
            r"(?:r\s*)?([0-9][0-9\s,.]*)([km])?\s*(?:per|a|each)\s*month\b",
            r"(?:r\s*)?([0-9][0-9\s,.]*)([km])?\s*(?:monthly\s+)?(?:contract|instalment|installment|payment)\b",
            r"(?:contract|instalment|installment|payment)\s+(?:of|for|at)?\s*(?:r\s*)?([0-9][0-9\s,.]*)([km])?\b",
        )
        for pattern in recurring_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            amount = cls._parse_amount(match.group(1), match.group(2))
            if amount is not None and amount >= 1:
                result["recurringPayment"] = amount
                result["paymentFrequency"] = "month"
                result["commitmentType"] = "recurring"
                break

        if "recurringPayment" not in result:
            # A plain price can still be useful for affordability. Do not invent one
            # when the text clearly describes a recurring contract.
            amount = cls._extract_target_amount(original)
            if amount is None:
                candidates = cls._extract_money_candidates(original, [])
                if candidates:
                    amount = max(value for value, _ in candidates)
            if amount is not None and amount >= 1:
                result["purchasePrice"] = round(amount, 2)
                result["commitmentType"] = "once_off"
        return result

    @classmethod
    def calculate_goal(cls, entities: dict[str, Any]) -> dict[str, Any] | None:
        action = entities.get("calculation")
        target = cls._as_positive_number(entities.get("targetAmount"))
        existing = cls._as_nonnegative_number(entities.get("existingSavings")) or 0.0
        months = cls._as_positive_number(entities.get("timeframeMonths"))
        contribution = cls._as_positive_number(entities.get("periodicContribution"))
        contribution_frequency = entities.get("contributionFrequency") or "month"
        requested_frequency = entities.get("requestedFrequency") or "month"

        if action == "required_contribution" and target is not None and months:
            remaining = max(0.0, target - existing)
            monthly = remaining / months
            weekly = remaining / max(1.0, months * 52 / 12)
            yearly = remaining / max(1.0, months / 12)
            if requested_frequency == "week":
                primary = weekly
            elif requested_frequency == "year":
                primary = yearly
            else:
                primary = monthly
            return {
                "action": action,
                "target": round(target, 2),
                "existing": round(existing, 2),
                "remaining": round(remaining, 2),
                "months": int(round(months)),
                "monthly": round(monthly, 2),
                "weekly": round(weekly, 2),
                "yearly": round(yearly, 2),
                "primary": round(primary, 2),
                "frequency": requested_frequency,
            }

        if action == "time_to_target" and target is not None and contribution:
            remaining = max(0.0, target - existing)
            monthly_contribution = cls._to_monthly(contribution, contribution_frequency)
            if monthly_contribution <= 0:
                return None
            months_needed = 0 if remaining <= 0 else math.ceil(remaining / monthly_contribution)
            return {
                "action": action,
                "target": round(target, 2),
                "existing": round(existing, 2),
                "remaining": round(remaining, 2),
                "contribution": round(contribution, 2),
                "contributionFrequency": contribution_frequency,
                "monthlyEquivalent": round(monthly_contribution, 2),
                "monthsNeeded": months_needed,
            }

        if action == "future_value" and months and contribution:
            monthly_contribution = cls._to_monthly(contribution, contribution_frequency)
            total = existing + monthly_contribution * months
            return {
                "action": action,
                "existing": round(existing, 2),
                "months": int(round(months)),
                "contribution": round(contribution, 2),
                "contributionFrequency": contribution_frequency,
                "monthlyEquivalent": round(monthly_contribution, 2),
                "futureValue": round(total, 2),
            }
        return None

    @classmethod
    def _goal_signal_score(cls, text: str, entities: dict[str, Any]) -> int:
        score = 0
        if cls._looks_like_goal(text):
            score += 2
        if re.search(r"\bhow much\b.*\bsav", text):
            score += 2
        if re.search(r"\b(?:need|must|should)\b.*\bsav", text):
            score += 1
        if re.search(r"\bsav(?:e|ing)\b.*\b(?:in|over|within|by)\b", text):
            score += 1
        if entities.get("targetAmount") is not None:
            score += 1
        if entities.get("timeframeMonths") is not None:
            score += 1
        if entities.get("periodicContribution") is not None:
            score += 1
        if entities.get("subject"):
            score += 1
        return score

    @classmethod
    def _looks_like_goal(cls, text: str) -> bool:
        return bool(
            re.search(r"\b(?:save|saving|saved up|set aside|put away)\b", text)
            or re.search(r"\b(?:buy|purchase|get)\b.*\b(?:in|within|over|by)\b", text)
            or re.search(r"\bgoal\b", text)
            or re.search(r"\bhow long\b.*\b(?:reach|save|afford|buy)\b", text)
        )

    @classmethod
    def _factual_intent(cls, text: str) -> str | None:
        if re.search(r"\b(?:account\s+)?balance\b", text) or "how much money do i have" in text:
            return "balance"
        if any(phrase in text for phrase in ("largest expense", "biggest expense", "largest purchase", "biggest purchase", "most expensive purchase")):
            return "largest_expense"
        if re.search(r"\bhow many\s+(?:transactions|purchases|expenses)\b", text):
            return "transaction_count"
        if any(phrase in text for phrase in ("did i spend more", "did i spend less", "spending higher", "spending lower", "spent more this", "spent less this")):
            return "spending_comparison"
        if any(phrase in text for phrase in ("where is my money going", "where did my money go", "top spending categories", "biggest categories")):
            return "top_categories"
        if ("category" in text and any(word in text for word in ("most", "highest", "top", "largest"))) or "spend most on" in text:
            return "top_category"
        if "merchant" in text and any(word in text for word in ("most", "highest", "top", "largest")):
            return "top_merchant"
        if cls._is_spending_history(text):
            return "spending_total"
        if cls._is_income_history(text):
            return "income_total"
        if cls._is_savings_history(text):
            return "savings_total"
        return None

    @staticmethod
    def _is_spending_history(text: str) -> bool:
        return bool(
            re.search(r"\bhow much\b.*\b(?:did i )?(?:spend|spent|spending)\b", text)
            or "what did i spend" in text
            or "total spending" in text
        )

    @staticmethod
    def _is_income_history(text: str) -> bool:
        return bool(
            re.search(r"\bhow much\b.*\b(?:income|earn|earned|receive|received|came in)\b", text)
            or "total income" in text
            or "money came in" in text
        )

    @staticmethod
    def _is_savings_history(text: str) -> bool:
        # Crucially, "how much do I need to save" is not savings history.
        if re.search(r"\b(?:need|must|should|want|plan)\b.*\bsav", text):
            return False
        if re.search(r"\bsav(?:e|ing)\b.*\b(?:in|over|within)\s+\d+\s*(?:months?|years?|yrs?)\b", text):
            return False
        return bool(
            re.search(r"\bhow much\b.*\b(?:did i save|have i saved|saved|net savings|savings movement)\b", text)
            or "net savings" in text
            or "total saved" in text
            or re.search(r"\b(?:my )?savings\b.*\b(?:last|this|previous|january|february|march|april|may|june|july|august|september|october|november|december)\b", text)
        )

    @staticmethod
    def _advice_intent(text: str) -> str:
        if "budget" in text:
            return "budget_analysis"
        if "save" in text or "saving" in text or "goal" in text:
            return "saving_goal"
        if "spend" in text or "expense" in text:
            return "spending_analysis"
        return "general_finance"

    @classmethod
    def _extract_subject(cls, text: str) -> str | None:
        for subject in sorted(cls.GOAL_SUBJECTS, key=len, reverse=True):
            if re.search(rf"\b{re.escape(subject)}\b", text):
                return subject
        return None

    @staticmethod
    def _extract_duration(text: str) -> tuple[int | None, tuple[int, int] | None]:
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*(years?|yrs?|months?|mos?)\b", text)
        if match:
            count = float(match.group(1))
            unit = match.group(2)
            months = count * 12 if unit.startswith(("year", "yr")) else count
            return max(1, int(round(months))), match.span()

        words = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "twelve": 12, "eighteen": 18, "twenty": 20, "twenty-four": 24,
            "thirty-six": 36,
        }
        word_pattern = "|".join(sorted((re.escape(word) for word in words), key=len, reverse=True))
        word_match = re.search(rf"\b({word_pattern})\s*(years?|yrs?|months?|mos?)\b", text)
        if not word_match:
            return None, None
        count = float(words[word_match.group(1)])
        unit = word_match.group(2)
        months = count * 12 if unit.startswith(("year", "yr")) else count
        return max(1, int(round(months))), word_match.span()

    @classmethod
    def _extract_periodic_contribution(cls, text: str) -> tuple[float | None, tuple[int, int] | None]:
        patterns = (
            r"\b(?:save|saving|put away|set aside)\s+(?:r\s*)?([\d][\d\s,.]*)([km])?\s*(?:per|a|each)\s+(month|week|year)\b",
            r"\b(?:r\s*)?([\d][\d\s,.]*)([km])?\s*(?:per|a|each)\s+(month|week|year)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = cls._parse_amount(match.group(1), match.group(2))
                if value is not None:
                    return value, match.span()
        return None, None

    @classmethod
    def _extract_existing_savings(cls, text: str) -> tuple[float | None, tuple[int, int] | None]:
        match = re.search(
            r"\b(?:already\s+(?:have|saved)|have\s+already\s+saved|currently\s+have|starting\s+with|i\s+have\s+saved)\s+(?:r\s*)?([\d][\d\s,.]*)([km])?\b",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None, None
        return cls._parse_amount(match.group(1), match.group(2)), match.span()

    @classmethod
    def _extract_target_amount(cls, text: str) -> float | None:
        patterns = (
            r"\b(?:target|goal)\s+(?:is|of|:)?\s*(?:r\s*)?([\d][\d\s,.]*)([km])?\b",
            r"\b(?:costs?|costing|worth|price(?:d)?\s+at|priced\s+at|that\s+is|for)\s+(?:r\s*)?([\d][\d\s,.]*)([km])?\b",
            r"\b(?:reach|save up|save)\s+(?:r\s*)?([\d][\d\s,.]*)([km])?\b",
        )
        lower = text.casefold()
        for pattern in patterns:
            match = re.search(pattern, lower, flags=re.IGNORECASE)
            if match:
                amount = cls._parse_amount(match.group(1), match.group(2))
                if amount is not None and amount >= 100:
                    return amount
        return None

    @classmethod
    def _extract_money_candidates(
        cls,
        text: str,
        excluded_spans: list[tuple[int, int]],
    ) -> list[tuple[float, tuple[int, int]]]:
        results: list[tuple[float, tuple[int, int]]] = []
        pattern = re.compile(r"(?<![a-z0-9])(?:r\s*)?([0-9][0-9\s,.]*)([kKmM])?\b")
        for match in pattern.finditer(text):
            span = match.span()
            if any(cls._overlap(span, excluded) for excluded in excluded_spans):
                continue
            amount = cls._parse_amount(match.group(1), match.group(2))
            if amount is None or amount < 100:
                continue
            # Four digit calendar years are not purchase amounts unless prefixed by R.
            token = match.group(0).strip().casefold()
            if 2000 <= amount <= 2100 and not token.startswith("r"):
                continue
            results.append((amount, span))
        return results

    @staticmethod
    def _requested_frequency(text: str) -> str:
        if re.search(r"\b(?:per|a|each)\s+week\b|\bweekly\b", text):
            return "week"
        if re.search(r"\b(?:per|a|each)\s+year\b|\byearly\b|\bannually\b", text):
            return "year"
        return "month"

    @staticmethod
    def _contribution_frequency(text: str) -> str:
        if re.search(r"\b(?:per|a|each)\s+week\b|\bweekly\b", text):
            return "week"
        if re.search(r"\b(?:per|a|each)\s+year\b|\byearly\b|\bannually\b", text):
            return "year"
        return "month"

    @staticmethod
    def _memory_state(summary: dict[str, Any]) -> dict[str, Any]:
        state = summary.get("state") if isinstance(summary, dict) else None
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _parse_amount(raw: str | None, suffix: str | None = None) -> float | None:
        if not raw:
            return None
        cleaned = raw.replace(" ", "").replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            return None
        suffix = (suffix or "").casefold()
        if suffix == "k":
            value *= 1000
        elif suffix == "m":
            value *= 1_000_000
        return round(value, 2)

    @staticmethod
    def _to_monthly(value: float, frequency: str) -> float:
        if frequency == "week":
            return value * 52 / 12
        if frequency == "year":
            return value / 12
        return value

    @staticmethod
    def _as_positive_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _as_nonnegative_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    @staticmethod
    def _overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
        return left[0] < right[1] and right[0] < left[1]

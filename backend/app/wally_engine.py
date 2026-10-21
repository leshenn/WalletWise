from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .ai_context import AIContextBuilder
from .ai_response import WalletWiseAIResponse
from .ai_service import AIConfigurationError, AIProviderError, GroqAIService
from .reporting import ReportingService
from .wally_query_plan import DeterministicQueryPlanner, QueryPlan, QueryPlanValidator, with_ai_fallback
from .wally_query_planner import WallyAIQueryPlanner
from .wally_router import RouteDecision, SmartLocalRouter
from .wally_trace import header, line, path, section


@dataclass
class WallyResult:
    answer: dict[str, Any]
    data_used: dict[str, Any]
    provider: str
    model: str
    ai_used: bool


class WallyAnswerEngine:
    """Hybrid WalletWise assistant.

    Level 0: deterministic factual questions are answered from WalletWise data
    without an external model call.

    Level 1/2: questions that need explanation, planning or judgement use the
    LLM, but only after Python has reduced the user's data to a small,
    intent-specific context.
    """

    MONTHS = {
        "january": 1, "jan": 1,
        "february": 2, "feb": 2,
        "march": 3, "mar": 3,
        "april": 4, "apr": 4,
        "may": 5,
        "june": 6, "jun": 6,
        "july": 7, "jul": 7,
        "august": 8, "aug": 8,
        "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10,
        "november": 11, "nov": 11,
        "december": 12, "dec": 12,
    }

    # These words generally mean the user wants interpretation/advice rather
    # than a database fact. Keep this conservative: uncertainty falls back to AI.
    REASONING_MARKERS = (
        "why", "how can", "how should", "what should", "should i", "can i afford",
        "afford", "recommend", "advice", "plan", "realistic", "realistically",
        "help me", "cut back", "reduce", "improve", "better", "goal", "save for",
        "save up", "what can i do", "what do you think", "worth it",
    )

    @classmethod
    def answer(
        cls,
        *,
        question: str,
        transactions: list[dict],
        settings: dict | None = None,
        coverage: dict | None = None,
        history: list[dict] | None = None,
        conversation_summary: dict[str, Any] | None = None,
        trace_id: str = "--------",
    ) -> WallyResult:
        settings = settings or {}
        coverage = coverage or {}
        history = history or []
        conversation_summary = conversation_summary or {}

        memory_eligible = SmartLocalRouter.is_contextual_followup(question)
        routing_summary = conversation_summary if memory_eligible else {}

        # 1) Build a semantic plan from the current question. This is free.
        plan = DeterministicQueryPlanner.build(question, routing_summary)
        planner_used = False
        planner_model: str | None = None
        planner_usage: dict[str, Any] = {}

        section(trace_id, "ROUTING 1/7 - CURRENT QUESTION + MEMORY POLICY", {
            "question": question,
            "isGenuineFollowUp": memory_eligible,
            "savedChatMemoryUsedForRouting": memory_eligible,
            "savedChatMemoryIgnored": bool(conversation_summary) and not memory_eligible,
            "memoryRule": "Current question wins. Saved memory can only fill omitted details in a genuine follow-up.",
            "memoryStateAvailable": (routing_summary.get("state", {}) if isinstance(routing_summary, dict) else {}),
        })
        section(trace_id, "ROUTING 2/7 - DETERMINISTIC SEMANTIC PLAN (0 AI TOKENS)", plan.to_dict())

        # 2) Only ambiguous language uses the tiny AI planner. No financial data.
        if plan.route == "classify":
            memory_state = routing_summary.get("state") if isinstance(routing_summary, dict) else {}
            try:
                planned = WallyAIQueryPlanner.plan(
                    question=question,
                    memory_state=memory_state if isinstance(memory_state, dict) else {},
                )
                planner_used = True
                planner_model = planned.model
                planner_usage = planned.usage or {}
                section(trace_id, "ROUTING 3/7 - TINY AI QUERY PLANNER REQUEST", {
                    "purpose": "Understand the wording and produce a query plan only. No transactions, balances, reports or statement data are sent.",
                    "requestBody": planned.request_body,
                    "providerPayload": planned.provider_payload,
                    "apiKeyIncludedInTerminal": False,
                })
                section(trace_id, "ROUTING 3/7 - TINY AI QUERY PLANNER RESPONSE", {
                    "raw": planned.raw_response,
                    "validatedPlan": planned.plan.to_dict(),
                    "actualTokenUsage": planner_usage,
                })
                plan = cls._merge_query_plans(plan, planned.plan, question)
                if plan.confidence < WallyAIQueryPlanner.confidence_threshold():
                    plan = with_ai_fallback(
                        plan,
                        f"Query-planner confidence {plan.confidence:.2f} was below the safety threshold; use full AI reasoning instead of risking a wrong local answer.",
                    )
            except (AIConfigurationError, AIProviderError) as exc:
                section(trace_id, "ROUTING 3/7 - QUERY PLANNER UNAVAILABLE", str(exc))
                plan = with_ai_fallback(
                    plan,
                    "The tiny query planner was unavailable, so WalletWise chose the safe full-AI fallback rather than guessing locally.",
                )
        else:
            section(trace_id, "ROUTING 3/7 - TINY AI QUERY PLANNER", {
                "used": False,
                "reason": "The deterministic semantic plan was already high-confidence.",
                "tokens": 0,
            })

        # 3) Validate semantics before execution. Invalid local plans never run.
        validation = QueryPlanValidator.validate(question, plan)
        section(trace_id, "ROUTING 4/7 - QUERY PLAN VALIDATION", {
            "finalPlanBeforeExecution": plan.to_dict(),
            **validation.to_dict(),
        })
        if not validation.valid:
            plan = with_ai_fallback(
                plan,
                "Local query plan failed semantic validation: " + "; ".join(validation.issues),
            )
            section(trace_id, "ROUTING 4/7 - SAFETY FALLBACK", {
                "action": "Do not execute the inconsistent local plan.",
                "newPlan": plan.to_dict(),
            })

        planner_meta = cls._planner_metadata(planner_used, planner_model, planner_usage)

        # A planner may explicitly request clarification. Do that locally.
        if plan.route == "clarify":
            answer, data_used = cls._clarification_from_plan(plan)
            answer = WalletWiseAIResponse.model_validate(answer).model_dump(mode="json")
            data_used.update({
                "answerMode": "local",
                "answerGeneratedByAI": False,
                "aiUsed": planner_used,
                "financialDataSentExternally": False,
                "queryPlan": plan.to_dict(),
                **planner_meta,
            })
            section(trace_id, "ROUTING 5/7 - LOCAL CLARIFICATION", {"answer": answer, "dataUsed": data_used})
            section(trace_id, "ROUTING 6/7 - ANSWER VALIDATION", {"valid": True, "reason": "Clarification does not perform a financial calculation."})
            section(trace_id, "ROUTING 7/7 - FINAL PATH", {"path": ["frontend", "backend", "semantic plan", "plan validation", "local clarification", "frontend"]})
            return WallyResult(answer=answer, data_used=data_used, provider="WalletWise local", model=planner_model or "deterministic", ai_used=planner_used)

        # 4) Execute valid local plans. Python owns maths and aggregation.
        if plan.route == "local":
            if plan.measure != "goal" and not transactions:
                raise ValueError("Upload at least one bank statement before asking Wally this financial question.")

            local = cls._execute_query_plan(
                question=question,
                plan=plan,
                transactions=transactions,
                settings=settings,
                coverage=coverage,
                conversation_summary=conversation_summary,
            )
            if local is not None:
                answer, data_used, execution_debug = local
                answer = WalletWiseAIResponse.model_validate(answer).model_dump(mode="json")
                answer_validation = QueryPlanValidator.validate_answer(plan, data_used)
                section(trace_id, "ROUTING 5/7 - LOCAL PYTHON EXECUTION", execution_debug)
                section(trace_id, "ROUTING 6/7 - ANSWER VALIDATION", answer_validation.to_dict())
                if answer_validation.valid:
                    data_used.update({
                        "answerMode": "local",
                        "answerGeneratedByAI": False,
                        "aiUsed": planner_used,
                        "providerRequestMade": planner_used,
                        "financialDataSentExternally": False,
                        "savedConversationMessagesSent": 0,
                        "conversationSummarySent": False,
                        "queryPlan": plan.to_dict(),
                        "routerIntent": plan.ai_intent or plan.measure,
                        "routerConfidence": plan.confidence,
                        "routerReason": plan.reason,
                        "routerSource": plan.source,
                        **planner_meta,
                    })
                    section(trace_id, "ROUTING 7/7 - FINAL PATH", {
                        "path": [
                            "frontend", "backend", "deterministic semantic planner",
                            *( ["tiny AI query planner"] if planner_used else [] ),
                            "plan validator", "local Python executor", "answer validator", "frontend",
                        ],
                        "answer": answer,
                        "aiAnswerGenerationTokens": 0,
                    })
                    return WallyResult(
                        answer=answer, data_used=data_used,
                        provider="WalletWise local + Groq planner" if planner_used else "WalletWise local",
                        model=planner_model or "deterministic", ai_used=planner_used,
                    )
                plan = with_ai_fallback(
                    plan,
                    "The local result did not match the semantic query plan, so it was rejected before reaching the user.",
                )
            else:
                section(trace_id, "ROUTING 5/7 - LOCAL EXECUTION COULD NOT RESOLVE", {
                    "plan": plan.to_dict(),
                    "action": "Reject local guess and use the full AI path.",
                })
                plan = with_ai_fallback(
                    plan,
                    "The validated local plan could not be executed safely with the available data.",
                )

        if not transactions:
            raise ValueError("Upload at least one bank statement before asking Wally this financial question.")

        # 5) AI path: Python still prepares only the relevant, sanitised facts.
        use_memory = memory_eligible
        ai_history = history if use_memory else []
        ai_summary = conversation_summary if use_memory else {}
        ai_intent = plan.ai_intent or cls._ai_intent_from_plan(plan)
        context, data_used = AIContextBuilder.build(
            question=question,
            transactions=transactions,
            settings=settings,
            coverage=coverage,
            history=ai_history,
            conversation_summary=ai_summary,
            intent_override=ai_intent,
            entity_hints=plan.entities,
        )
        data_used.update({
            "answerMode": "ai",
            "answerGeneratedByAI": True,
            "aiUsed": True,
            "providerRequestMade": True,
            "financialDataSentExternally": True,
            "savedConversationMessagesSent": len(ai_history),
            "conversationSummarySent": bool(ai_summary),
            "conversationMemoryNeeded": use_memory,
            "queryPlan": plan.to_dict(),
            "routerIntent": ai_intent,
            "routerConfidence": plan.confidence,
            "routerReason": plan.reason,
            "routerSource": plan.source,
            **planner_meta,
        })
        section(trace_id, "ROUTING 5/7 - AI CONTEXT BUILDER", {
            "queryPlan": plan.to_dict(),
            "sanitisedFinancialContext": context,
            "conversationSummarySent": ai_summary,
            "recentConversationSent": ai_history,
            "note": "Raw statement PDF, account/card numbers and raw bank descriptions are not sent.",
        })

        ai_result = GroqAIService.ask(
            question=question,
            context=context,
            history=ai_history,
            conversation_summary=ai_summary,
            trace_id=trace_id,
        )
        answer_usage = ai_result.usage or {}
        data_used.update(cls._answer_usage_metadata(answer_usage))
        data_used["totalProviderTokens"] = int(data_used.get("classifierTotalTokens") or 0) + int(data_used.get("answerTotalTokens") or 0)

        section(trace_id, "ROUTING 6/7 - FULL AI REQUEST + RESPONSE", {
            "provider": "Groq",
            "model": ai_result.model,
            "providerPayloadSent": ai_result.provider_payload,
            "authorizationHeaderShown": False,
            "rawModelResponse": ai_result.raw_answer,
            "validatedAnswer": ai_result.answer,
            "actualTokenUsage": answer_usage,
            "automaticTruncationRetryUsed": ai_result.retry_used,
        })
        section(trace_id, "ROUTING 7/7 - FINAL PATH", {
            "path": [
                "frontend", "backend", "deterministic semantic planner",
                *( ["tiny AI query planner"] if planner_used else [] ),
                "plan validator", "local context builder", "Groq answer", "structured-response validation", "frontend",
            ],
            "answer": ai_result.answer,
            "totalProviderTokens": data_used.get("totalProviderTokens"),
        })
        return WallyResult(
            answer=ai_result.answer, data_used=data_used, provider="Groq", model=ai_result.model, ai_used=True,
        )

    @staticmethod
    def _planner_metadata(used: bool, model: str | None, usage: dict[str, Any]) -> dict[str, Any]:
        prompt = int((usage or {}).get("prompt_tokens") or 0)
        completion = int((usage or {}).get("completion_tokens") or 0)
        total = int((usage or {}).get("total_tokens") or (prompt + completion))
        # Keep the old classifier fields for frontend compatibility while exposing
        # the new semantic-planner names as well.
        return {
            "classifierUsed": used,
            "classifierModel": model if used else None,
            "classifierInputTokens": prompt,
            "classifierOutputTokens": completion,
            "classifierTotalTokens": total,
            "queryPlannerUsed": used,
            "queryPlannerModel": model if used else None,
            "queryPlannerInputTokens": prompt,
            "queryPlannerOutputTokens": completion,
            "queryPlannerTotalTokens": total,
        }

    @classmethod
    def _merge_query_plans(cls, rule_plan: QueryPlan, ai_plan: QueryPlan, question: str) -> QueryPlan:
        """Merge only facts the tiny planner omitted; current-message AI extraction wins.

        The deterministic plan may already know an explicit month/scope. Saved memory
        is never allowed to overwrite explicit current-question entities.
        """
        entities = dict(rule_plan.entities or {})
        entities.update(ai_plan.entities or {})
        intent = ai_plan.ai_intent or rule_plan.ai_intent
        if intent:
            normalise_intent = intent
        elif ai_plan.measure == "goal":
            normalise_intent = "saving_goal_calculation"
        elif ai_plan.measure == "affordability":
            normalise_intent = "affordability"
        else:
            normalise_intent = "general_finance"
        entities = cls._normalise_classifier_entities(
            question=question,
            intent=normalise_intent,
            rule_entities=entities,
            classified_entities={},
        )
        return QueryPlan(
            route=ai_plan.route,
            query_type=ai_plan.query_type,
            measure=ai_plan.measure,
            operation=ai_plan.operation,
            group_by=ai_plan.group_by,
            scope=ai_plan.scope if ai_plan.scope != "none" else rule_plan.scope,
            period=ai_plan.period or rule_plan.period,
            direction=ai_plan.direction or rule_plan.direction,
            limit=ai_plan.limit or rule_plan.limit,
            breakdown_by_period=ai_plan.breakdown_by_period or rule_plan.breakdown_by_period,
            entities=entities,
            ai_intent=ai_plan.ai_intent or rule_plan.ai_intent,
            confidence=ai_plan.confidence,
            reason=ai_plan.reason,
            source="ai_query_planner",
        )

    @staticmethod
    def _ai_intent_from_plan(plan: QueryPlan) -> str:
        if plan.ai_intent:
            return plan.ai_intent
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

    @classmethod
    def _clarification_from_plan(cls, plan: QueryPlan) -> tuple[dict[str, Any], dict[str, Any]]:
        entities = plan.entities or {}
        if plan.measure == "goal":
            if not entities.get("targetAmount"):
                question = "What amount are you aiming to save?"
            elif not entities.get("timeframeMonths"):
                question = "How long do you want to give yourself to reach that goal?"
            else:
                question = "What detail would you like me to use for this savings goal?"
        elif plan.measure == "spending" and plan.group_by == "category":
            question = "Which period would you like me to analyse?"
        else:
            question = "Could you give me one more detail about what you want me to calculate?"
        answer = cls._answer(
            title="I need one more detail",
            summary="I can work this out once I have the missing detail.",
            status="insufficient_information",
            needs_user_input=True,
            follow_up_question=question,
        )
        return answer, {
            "intent": plan.ai_intent or plan.measure,
            "localFactType": "clarification",
            "periodsIncluded": 0,
            "monthsIncluded": 0,
            "transactionDetailsIncluded": 0,
            "rawDescriptionsSent": False,
            "statementPdfSent": False,
            "accountOrCardNumbersSent": False,
            "contextSections": [],
        }

    @classmethod
    def _execute_query_plan(
        cls,
        *,
        question: str,
        plan: QueryPlan,
        transactions: list[dict],
        settings: dict,
        coverage: dict,
        conversation_summary: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        # Goal arithmetic can run without a statement.
        if plan.measure == "goal":
            route = RouteDecision(
                "saving_goal_calculation" if plan.route == "local" else "saving_goal_missing_info",
                "local", plan.confidence, plan.reason, plan.entities, source=plan.source,
            )
            result = cls._try_goal_answer(question, route)
            if result is None:
                return None
            answer, data_used = result
            return answer, data_used, {
                "executor": "goal_calculator",
                "operation": plan.operation,
                "inputs": plan.entities,
                "financialStatementRowsRead": 0,
                "externalAIAnswerCall": False,
            }

        reports = ReportingService.build(transactions, settings, coverage)

        # Explicit dates are binding. A missing July must never silently become
        # the latest available month (or all imported history).
        if plan.scope == "specific_period" and plan.period:
            match = cls._resolve_named_month(plan.period, reports)
            if match is None:
                expected_fact = (
                    "category_or_merchant_spend" if plan.operation == "sum" and plan.group_by == "category" else
                    "income_total" if plan.measure == "income" else
                    "savings_total" if plan.measure == "savings" else
                    "spending_total" if plan.operation == "sum" else
                    "transaction_count" if plan.operation == "count" else
                    "largest_expense" if plan.operation == "max_transaction" else
                    "smallest_expense" if plan.operation == "min_transaction" else
                    "category_ranking" if plan.operation == "rank" and plan.group_by == "category" else
                    "merchant_ranking" if plan.operation == "rank" else
                    "category_trend" if plan.operation == "compare" and plan.group_by == "category" else
                    "spending_comparison"
                )
                answer = cls._answer(
                    title="Month not available",
                    summary=f"I couldn't find statement data for {plan.period.title()}. I won't substitute a different month.",
                    status="insufficient_information",
                    warnings=["Import the relevant statement or check the month and year."],
                )
                return answer, {
                    "intent": plan.ai_intent or plan.measure, "localFactType": expected_fact,
                    "periodsIncluded": 0, "monthsIncluded": 0,
                    "periodResolvedFrom": "not_found", "transactionDetailsIncluded": 0,
                    "contextSections": [], "rawDescriptionsSent": False,
                    "statementPdfSent": False, "accountOrCardNumbersSent": False,
                }, {"executor": "date_guard", "requestedPeriod": plan.period, "result": "not_found"}

        if plan.operation == "min_transaction":
            scoped, periods, label = cls._transactions_for_query_scope(plan, question, transactions, reports, settings)
            expenses = [row for row in scoped if row.get("financialRole") == "expense"
                        and cls._num(row.get("amount")) < 0]
            if expenses:
                smallest = min(expenses, key=lambda row: abs(cls._num(row.get("amount"))))
                amount = abs(cls._num(smallest.get("amount")))
                name = str(smallest.get("displayName") or "Expense")
                answer = cls._answer(
                    title="Smallest expense",
                    summary=f"Your smallest recorded expense in {label} was {cls._money(amount)} at {name} on {smallest.get('date')}.",
                    status="neutral", metrics=[cls._metric("Amount", cls._money(amount))],
                )
            else:
                answer = cls._answer(
                    title="No expense identified",
                    summary=f"I couldn't identify any expense transactions in {label}.",
                    status="insufficient_information",
                )
            return answer, {
                "intent": "spending_analysis", "localFactType": "smallest_expense",
                "periodsIncluded": len(periods), "monthsIncluded": len(periods),
                "transactionDetailsIncluded": 1 if expenses else 0,
                "rawDescriptionsSent": False, "statementPdfSent": False,
                "accountOrCardNumbersSent": False, "contextSections": ["expense transactions"],
                "periodResolvedFrom": plan.scope,
            }, {"executor": "minimum_expense", "scope": plan.scope,
                "transactionId": str(smallest.get("id")) if expenses else None}

        if plan.operation == "sum" and plan.measure == "spending" and plan.group_by == "category" and plan.entities.get("category"):
            return cls._execute_category_sum_plan(plan, question, transactions, reports, settings)

        if plan.operation == "compare" and (
            plan.scope == "past_complete_periods" or plan.group_by == "category"
        ):
            # This dedicated executor filters by subject and compares actual
            # consecutive calendar months, rather than calling the generic
            # two-period overall-spending handler.
            return cls._execute_trend_plan(plan, transactions, reports)

        if plan.operation == "rank" and plan.group_by in {"category", "merchant"}:
            ranked = cls._execute_ranking_plan(plan, question, transactions, reports, settings)
            if ranked is None:
                return None
            return ranked

        intent = None
        if plan.operation == "max_transaction":
            intent = "largest_expense"
        elif plan.operation == "count":
            intent = "transaction_count"
        elif plan.operation == "latest" and plan.measure == "balance":
            intent = "balance"
        elif plan.operation == "compare" and plan.measure == "spending":
            intent = "spending_comparison"
        elif plan.operation == "sum" and plan.measure == "spending":
            intent = "category_spending" if plan.group_by == "category" else "merchant_spending" if plan.group_by == "merchant" else "spending_total"
        elif plan.operation == "sum" and plan.measure == "income":
            intent = "income_total"
        elif plan.operation == "sum" and plan.measure == "savings":
            intent = "savings_total"
        if intent is None:
            return None

        scoped_question = question
        if plan.scope == "all_history":
            scoped_question += " all time"
        elif plan.scope == "specific_period" and plan.period:
            scoped_question += f" {plan.period}"
        elif plan.scope == "previous_complete_period":
            scoped_question += " previous period"
        elif plan.scope == "latest_complete_period":
            scoped_question += " last complete period"
        elif plan.scope == "latest_period":
            scoped_question += " current month"

        route = RouteDecision(intent, "local", plan.confidence, plan.reason, plan.entities, source=plan.source)
        result = cls._try_local_answer(
            question=scoped_question,
            transactions=transactions,
            settings=settings,
            coverage=coverage,
            conversation_summary=conversation_summary,
            route=route,
        )
        if result is None:
            return None
        answer, data_used = result
        return answer, data_used, {
            "executor": "existing_local_finance_handler",
            "semanticOperation": plan.operation,
            "handler": intent,
            "scopeRequested": plan.scope,
            "periodRequested": plan.period,
            "periodResolvedFrom": data_used.get("periodResolvedFrom"),
            "resultType": data_used.get("localFactType"),
            "externalAIAnswerCall": False,
        }

    @classmethod
    def _resolve_named_month(cls, period_text: str, reports: dict) -> dict | None:
        for name, month_number in cls.MONTHS.items():
            if not re.search(rf"\b{re.escape(name)}\b", period_text.casefold()):
                continue
            year_match = re.search(r"\b(20\d{2})\b", period_text)
            year = int(year_match.group(1)) if year_match else None
            matching = [p for p in (reports.get("monthly") or [])
                        if str(p.get("month") or "")[5:7] == f"{month_number:02d}"
                        and (year is None or str(p.get("month")).startswith(str(year)))]
            return matching[-1] if matching else None
        return None

    @classmethod
    def _execute_category_sum_plan(
        cls, plan: QueryPlan, question: str, transactions: list[dict], reports: dict, settings: dict
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        category = str(plan.entities["category"])
        items, periods, label = cls._transactions_for_query_scope(plan, question, transactions, reports, settings)
        matching = [row for row in items if row.get("financialRole") == "expense"
                    and cls._num(row.get("amount")) < 0
                    and str(row.get("category") or "").casefold() == category.casefold()]
        base = round(sum(abs(cls._num(row.get("amount"))) for row in matching), 2)
        additional = 0.0
        fees_count = 0
        if category.casefold() == "bank fees":
            for row in items:
                is_fee_debit = (row.get("financialRole") == "expense"
                                and cls._num(row.get("amount")) < 0
                                and str(row.get("category") or "").casefold() == "bank fees")
                if not is_fee_debit and row.get("transactionType") != "closing_balance":
                    fee = abs(cls._num(row.get("bankCharges")))
                    if fee > 0:
                        additional += fee
                        fees_count += 1
        total = round(base + additional, 2)
        found = bool(matching or fees_count)
        warning = ("The result depends on how correctly statement fees and transactions were classified."
                   if category.casefold() == "bank fees" else
                   "The result depends on transaction categorisation.")
        answer = cls._answer(
            title=f"{category} spending" if found else f"{category} not identified",
            summary=(f"You spent {cls._money(total)} on {category} across {label}." if found
                     else f"I couldn't identify any {category} transactions in {label}. This is not proof that the amount was zero."),
            status="neutral" if found else "insufficient_information",
            metrics=([cls._metric("Total", cls._money(total)), cls._metric("Transactions", str(len(matching) + fees_count))] if found else []),
            warnings=[warning],
        )
        meta = {
            "intent": "category_analysis", "localFactType": "category_or_merchant_spend",
            "categoryFilter": category, "periodsIncluded": len(periods),
            "monthsIncluded": len(periods), "transactionDetailsIncluded": 0,
            "contextSections": ["verified category expenses", "separate bank charges" if additional else ""],
            "periodResolvedFrom": plan.scope, "rawDescriptionsSent": False,
            "statementPdfSent": False, "accountOrCardNumbersSent": False,
        }
        debug = {"executor": "category_sum", "category": category, "scope": plan.scope,
                 "scopeLabel": label, "categoryDebitTotal": base,
                 "additionalBankCharges": round(additional, 2), "matchedExpenses": len(matching),
                 "matchedSeparateFeeFields": fees_count, "result": total if found else None}
        return answer, meta, debug

    @classmethod
    def _execute_trend_plan(
        cls, plan: QueryPlan, transactions: list[dict], reports: dict
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Local, auditable monthly trend. Never substitute overall spend for a category.

        Reported FNB bankCharges are additional per-transaction fees, not
        necessarily separate debit transactions. Include them on non-Bank-Fees
        rows, but never add charges on an already categorised Bank Fees debit.
        """
        category = str(plan.entities.get("category") or "").strip() if plan.group_by == "category" else None
        count = max(2, min(int(plan.entities.get("periodCount") or 3), 12))
        months = sorted(reports.get("monthly") or [], key=lambda item: str(item.get("month") or ""))
        # 'covered' means >=80% of days in the existing coverage model, not
        # truly 100% complete. That limitation must be disclosed.
        eligible = [m for m in months if m.get("coverageStatus") == "covered"]
        if plan.scope == "specific_period" and plan.period:
            month_name = next((name for name in cls.MONTHS if re.search(rf"\b{re.escape(name)}\b", plan.period.casefold())), None)
            year_match = re.search(r"\b(20\d{2})\b", plan.period)
            if month_name:
                targets = [m for m in eligible if int(str(m.get("month"))[5:7]) == cls.MONTHS[month_name]
                           and (not year_match or str(m.get("month")).startswith(year_match.group(1)))]
                if targets:
                    endpoint = str(targets[-1].get("month"))
                    eligible = [m for m in eligible if str(m.get("month")) <= endpoint]
                else:
                    eligible = []  # Explicit month not covered; do not fall back to latest.
        chosen = eligible[-count:]
        excluded = [m.get("month") for m in months if m.get("coverageStatus") != "covered"]
        evidence: list[dict[str, Any]] = []
        for month in chosen:
            month_items = cls._transactions_for_period(transactions, month)
            total = 0.0
            matched = 0
            charge_count = 0
            for row in month_items:
                fee_category = str(row.get("category") or "").casefold() == "bank fees"
                expense = row.get("financialRole") == "expense" and cls._num(row.get("amount")) < 0
                if expense and (not category or str(row.get("category") or "").casefold() == category.casefold()):
                    total += abs(cls._num(row.get("amount")))
                    matched += 1
                if category == "Bank Fees" and (not fee_category or not expense) and row.get("transactionType") != "closing_balance":
                    # Per-transaction bank charges can exist even when the
                    # underlying purchase is not itself a 'Bank Fees' expense.
                    charge = abs(cls._num(row.get("bankCharges")))
                    if charge > 0:
                        total += charge
                        charge_count += 1
            evidence.append({
                "month": str(month.get("month")), "label": cls._period_name(month),
                "amount": round(total, 2), "coveragePct": month.get("coveragePct"),
                "categoryExpenseCount": matched, "additionalChargeCount": charge_count,
            })

        data_used = {
            "intent": "category_analysis" if category else "spending_analysis",
            "localFactType": "category_trend",
            "periodsIncluded": len(chosen), "monthsIncluded": len(chosen),
            "transactionDetailsIncluded": 0, "rawDescriptionsSent": False,
            "statementPdfSent": False, "accountOrCardNumbersSent": False,
            "contextSections": ["monthly verified-coverage aggregates"],
            "periodResolvedFrom": plan.scope,
            "categoryFilter": category, "monthlyBreakdown": evidence,
        }
        debug = {
            "executor": "monthly_subject_trend", "operation": "filter -> monthly group -> compare adjacent months",
            "categoryFilter": category or "all confirmed expenses", "requestedMonths": count,
            "eligibleCoverageThreshold": "covered (80%+)", "excludedUncoveredMonths": excluded,
            "monthlyTotals": evidence, "externalAIAnswerCall": False,
        }
        label = category or "Overall spending"
        if len(chosen) < count:
            answer = cls._answer(
                title=f"{label} trend unavailable",
                summary=f"I can only verify {len(chosen)} sufficiently covered month(s) for this period, but this trend needs {count}. I can't reliably tell whether {label.lower()} has been increasing.",
                status="insufficient_information",
                metrics=[cls._metric(item["label"], cls._money(item["amount"])) for item in evidence[-3:]],
                warnings=["Only months with at least 80% statement coverage qualify. Import the missing periods or review your statement coverage."],
            )
            return answer, data_used, debug

        # If months are separated by an uncovered gap, the path between them is
        # unknowable. Do not claim a consecutive-month trend.
        def serial(yyyy_mm: str) -> int:
            year, month = (int(x) for x in yyyy_mm.split("-"))
            return year * 12 + month

        if any(serial(right["month"]) - serial(left["month"]) != 1 for left, right in zip(evidence, evidence[1:])):
            answer = cls._answer(
                title=f"{label} trend unavailable",
                summary="The available covered months have a gap, so I can't establish a reliable month-by-month trend.",
                status="insufficient_information",
                metrics=[cls._metric(item["label"], cls._money(item["amount"])) for item in evidence[-3:]],
                warnings=["Add the missing month's bank statement before interpreting the trend."],
            )
            return answer, data_used, debug

        if category == "Bank Fees" and not any(x["categoryExpenseCount"] or x["additionalChargeCount"] for x in evidence):
            answer = cls._answer(
                title="Bank fees not identified",
                summary="I found no transactions classified as Bank Fees and no separate bank charges in these months. That does not prove you paid no fees.",
                status="insufficient_information",
                warnings=["Review transaction categories and the statement bank-charges column."],
            )
            return answer, data_used, debug

        values = [x["amount"] for x in evidence]
        changes = [round(b - a, 2) for a, b in zip(values, values[1:])]
        if all(x > 0 for x in changes):
            trend, status, verdict = "increasing", "attention", "Yes"
        elif all(x < 0 for x in changes):
            trend, status, verdict = "decreasing", "improving", "No"
        elif all(x == 0 for x in changes):
            trend, status, verdict = "unchanged", "stable", "No"
        else:
            trend, status, verdict = "fluctuating", "neutral", "Not consistently"
        delta = round(values[-1] - values[0], 2)
        pct = cls._pct_change(values[0], values[-1])
        description = f" from {cls._money(values[0])} in {evidence[0]['label']} to {cls._money(values[-1])} in {evidence[-1]['label']}"
        answer = cls._answer(
            title=f"{label} trend",
            summary=f"{verdict}. {label} {('increased every month' if trend == 'increasing' else 'decreased every month' if trend == 'decreasing' else 'stayed unchanged' if trend == 'unchanged' else 'fluctuated')} across {count} consecutive covered months in your imported data{description}." if verdict != "Not consistently" else f"Not consistently. {label} fluctuated across {count} consecutive covered months in your imported data{description}.",
            status=status,
            metrics=[cls._metric(x["label"], cls._money(x["amount"])) for x in evidence[-4:]],
            insights=[cls._insight(f"{a['label']} → {b['label']}", f"{'Increased' if change > 0 else 'Decreased' if change < 0 else 'Unchanged'} by {cls._money(abs(change))}.") for a, b, change in zip(evidence, evidence[1:], changes)][-3:],
            warnings=["Covered months may contain up to 20% missing days; results also depend on correct transaction categorisation."]
            if any((row.get("coveragePct") or 0) < 100 for row in evidence) else
            ["Totals depend on how correctly imported bank transactions were categorised."],
        )
        # When the latest imported covered period is old, don't imply this is
        # a trend for the literal most recent months in the calendar.
        now = date.today()
        last_complete_calendar_month = now.year * 12 + now.month - 1
        latest_selected_month = serial(evidence[-1]["month"])
        if last_complete_calendar_month > latest_selected_month:
            answer["warnings"] = (answer.get("warnings") or [])[:1] + [
                f"Latest covered month is {evidence[-1]['label']}; more recent calendar months are not included."
            ]
        data_used.update({"trend": trend, "netChange": delta, "percentageChange": pct})
        debug.update({"trend": trend, "monthToMonthChanges": changes, "netChange": delta, "percentageChange": pct})
        return answer, data_used, debug

    @classmethod
    def _execute_ranking_plan(
        cls,
        plan: QueryPlan,
        question: str,
        transactions: list[dict],
        reports: dict,
        settings: dict,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        items, periods, scope_label = cls._transactions_for_query_scope(plan, question, transactions, reports, settings)
        expenses = [
            item for item in items
            if item.get("financialRole") == "expense" and cls._num(item.get("amount")) < 0
        ]
        if not expenses:
            return None

        key_name = "category" if plan.group_by == "category" else "displayName"
        grouped: dict[str, float] = {}
        for item in expenses:
            name = str(item.get(key_name) or ("Uncategorised" if plan.group_by == "category" else "Unknown merchant")).strip()
            grouped[name] = round(grouped.get(name, 0.0) + abs(cls._num(item.get("amount"))), 2)
        rows = sorted(
            ({"name": name, "amount": amount} for name, amount in grouped.items()),
            key=lambda row: row["amount"], reverse=True,
        )
        if not rows:
            return None

        top = rows[0]
        limit = max(1, min(int(plan.limit or 5), 5))
        visible = rows[:limit]
        noun = "category" if plan.group_by == "category" else "merchant"
        if plan.scope == "past_complete_periods":
            summary = f"Across {len(periods)} complete months, {top['name']} was your biggest spending {noun} at {cls._money(top['amount'])}."
        elif plan.scope == "all_history":
            summary = f"Across your imported history, {top['name']} was your biggest spending {noun} at {cls._money(top['amount'])}."
        else:
            summary = f"{top['name']} was your biggest spending {noun} in {scope_label} at {cls._money(top['amount'])}."

        metrics = [cls._metric(row["name"], cls._money(row["amount"])) for row in visible[:4]]
        insights = []
        per_period_debug = []
        if plan.breakdown_by_period and periods:
            # Show the most recent three period winners so the user can see how the
            # overall ranking relates to individual months without flooding the UI.
            for period in periods[-3:]:
                p_items = cls._transactions_for_period(transactions, period)
                p_expenses = [x for x in p_items if x.get("financialRole") == "expense" and cls._num(x.get("amount")) < 0]
                p_grouped: dict[str, float] = {}
                for item in p_expenses:
                    name = str(item.get(key_name) or ("Uncategorised" if plan.group_by == "category" else "Unknown merchant")).strip()
                    p_grouped[name] = round(p_grouped.get(name, 0.0) + abs(cls._num(item.get("amount"))), 2)
                if p_grouped:
                    winner_name, winner_amount = max(p_grouped.items(), key=lambda pair: pair[1])
                    label = cls._period_name(period)
                    insights.append(cls._insight(label, f"{winner_name} was highest at {cls._money(winner_amount)}."))
                    per_period_debug.append({"period": label, "winner": winner_name, "amount": winner_amount})

        answer = cls._answer(
            title="Top spending categories" if plan.group_by == "category" else "Top merchants",
            summary=summary,
            status="neutral",
            metrics=metrics,
            insights=insights,
        )
        data_used = {
            "intent": "category_analysis" if plan.group_by == "category" else "spending_analysis",
            "localFactType": "category_ranking" if plan.group_by == "category" else "merchant_ranking",
            "periodsIncluded": len(periods) if periods else (1 if plan.scope == "specific_period" else len(reports.get("monthly") or [])),
            "monthsIncluded": len(periods) if periods else (1 if plan.scope == "specific_period" else len(reports.get("monthly") or [])),
            "transactionDetailsIncluded": 0,
            "rawDescriptionsSent": False,
            "statementPdfSent": False,
            "accountOrCardNumbersSent": False,
            "contextSections": ["local grouped expense totals"],
            "periodResolvedFrom": plan.scope,
        }
        debug = {
            "executor": "semantic_aggregate_executor",
            "operation": "sum expenses -> group -> sort descending",
            "groupBy": plan.group_by,
            "scope": plan.scope,
            "scopeLabel": scope_label,
            "resolvedPeriods": [cls._period_name(p) for p in periods],
            "expenseTransactionsConsidered": len(expenses),
            "groupedTotals": visible,
            "perPeriodWinnersShown": per_period_debug,
            "externalAIAnswerCall": False,
        }
        return answer, data_used, debug

    @classmethod
    def _transactions_for_query_scope(
        cls,
        plan: QueryPlan,
        question: str,
        transactions: list[dict],
        reports: dict,
        settings: dict,
    ) -> tuple[list[dict], list[dict], str]:
        monthly = list(reports.get("monthly") or [])
        pay = list(reports.get("payCycles") or [])
        mode = str(settings.get("defaultTrackingView") or "calendar").casefold()
        tracking = pay if mode in {"pay_cycle", "pay-cycle", "paycycle"} and pay else monthly

        if plan.scope == "all_history":
            return list(transactions), [p for p in monthly if cls._period_complete(p)], "your imported history"

        if plan.scope == "past_complete_periods":
            # If the user says months, use calendar months even when Reports is in
            # pay-cycle mode. Otherwise follow the active reporting mode.
            source = monthly if "month" in SmartLocalRouter.normalize(question) and monthly else tracking
            complete = [p for p in source if cls._period_complete(p)]
            chosen = complete if complete else source
            selected: list[dict] = []
            seen: set[str] = set()
            for period in chosen:
                for item in cls._transactions_for_period(transactions, period):
                    key = str(item.get("id") or f"{item.get('date')}|{item.get('amount')}|{item.get('displayName')}")
                    if key not in seen:
                        seen.add(key)
                        selected.append(item)
            return selected, chosen, f"{len(chosen)} complete periods"

        if plan.scope == "specific_period":
            text = f"{question} {plan.period or ''}".strip()
            period, _ = cls._select_period(text, reports, settings)
            return cls._transactions_for_period(transactions, period), [period] if period else [], cls._period_name(period)

        periods = monthly if "month" in SmartLocalRouter.normalize(question) and monthly else tracking
        complete = [p for p in periods if cls._period_complete(p)]
        if plan.scope == "previous_complete_period":
            candidates = complete if len(complete) >= 2 else periods
            period = candidates[-2] if len(candidates) >= 2 else (candidates[-1] if candidates else None)
        elif plan.scope == "latest_period":
            period = periods[-1] if periods else None
        else:
            period = complete[-1] if complete else (periods[-1] if periods else None)
        return cls._transactions_for_period(transactions, period), [period] if period else [], cls._period_name(period)

    @staticmethod
    def _classifier_metadata(*, used: bool, model: str | None, usage: dict[str, Any]) -> dict[str, Any]:
        prompt = int((usage or {}).get("prompt_tokens") or 0)
        completion = int((usage or {}).get("completion_tokens") or 0)
        total = int((usage or {}).get("total_tokens") or (prompt + completion))
        return {
            "classifierUsed": used,
            "classifierModel": model if used else None,
            "classifierInputTokens": prompt,
            "classifierOutputTokens": completion,
            "classifierTotalTokens": total,
        }

    @staticmethod
    def _answer_usage_metadata(usage: dict[str, Any]) -> dict[str, Any]:
        prompt = int((usage or {}).get("prompt_tokens") or 0)
        completion = int((usage or {}).get("completion_tokens") or 0)
        total = int((usage or {}).get("total_tokens") or (prompt + completion))
        return {
            "answerInputTokens": prompt,
            "answerOutputTokens": completion,
            "answerTotalTokens": total,
        }

    @classmethod
    def _normalise_classifier_entities(
        cls,
        *,
        question: str,
        intent: str,
        rule_entities: dict[str, Any],
        classified_entities: dict[str, Any],
    ) -> dict[str, Any]:
        merged = {key: value for key, value in (rule_entities or {}).items() if value is not None}
        merged.update({key: value for key, value in (classified_entities or {}).items() if value is not None})

        if intent == "affordability":
            if merged.get("recurringPayment") is not None:
                # A contract/instalment is a recurring commitment, not a savings target.
                merged.pop("targetAmount", None)
                merged.pop("remainingAmount", None)
                merged.pop("calculation", None)
                merged.pop("missing", None)
            elif merged.get("purchasePrice") is None and merged.get("targetAmount") is not None:
                merged["purchasePrice"] = merged["targetAmount"]
            return merged

        if intent not in {"saving_goal_calculation", "saving_goal_missing_info"}:
            return merged

        if merged.get("targetAmount") is None and merged.get("purchasePrice") is not None:
            merged["targetAmount"] = merged["purchasePrice"]

        text = " ".join((question or "").casefold().split())
        target = merged.get("targetAmount")
        months = merged.get("timeframeMonths")
        contribution = merged.get("periodicContribution")
        if target is not None:
            existing = float(merged.get("existingSavings") or 0)
            merged["remainingAmount"] = round(max(0.0, float(target) - existing), 2)

        if re.search(r"\b(?:how long|how many months|how many years|when will i|when would i)\b", text) and target and contribution:
            merged["calculation"] = "time_to_target"
        elif re.search(r"\b(?:how much will i have|what will i have|how much would i have)\b", text) and months and contribution:
            merged["calculation"] = "future_value"
        elif target and months:
            merged["calculation"] = "required_contribution"

        missing: list[str] = []
        if merged.get("calculation") == "time_to_target":
            if not target:
                missing.append("target_amount")
            if not contribution:
                missing.append("periodic_contribution")
        elif merged.get("calculation") == "future_value":
            if not months:
                missing.append("timeframe")
            if not contribution:
                missing.append("periodic_contribution")
        elif not merged.get("calculation"):
            if not target:
                missing.append("target_amount")
            if not months:
                missing.append("timeframe")
        if missing:
            merged["missing"] = missing
        else:
            merged.pop("missing", None)
        return merged

    @staticmethod
    def _needs_conversation_memory(question: str) -> bool:
        # Kept as a compatibility helper for callers/tests. Routing and provider
        # context must use the exact same follow-up policy.
        return SmartLocalRouter.is_contextual_followup(question)

    @classmethod
    def _try_goal_answer(
        cls,
        question: str,
        route: RouteDecision,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if route.intent not in {"saving_goal_calculation", "saving_goal_missing_info"}:
            return None

        entities = route.entities or {}
        base_data = {
            "intent": "saving_goal_calculation",
            "localFactType": "saving_goal",
            "periodsIncluded": 0,
            "monthsIncluded": 0,
            "transactionDetailsIncluded": 0,
            "rawDescriptionsSent": False,
            "statementPdfSent": False,
            "accountOrCardNumbersSent": False,
            "contextSections": ["goal arithmetic"],
            "goalEntities": {
                key: value for key, value in entities.items()
                if key in {
                    "subject", "targetAmount", "existingSavings", "remainingAmount",
                    "timeframeMonths", "periodicContribution", "contributionFrequency",
                    "requestedFrequency", "calculation", "missing",
                }
            },
        }

        missing = entities.get("missing") or []
        if route.intent == "saving_goal_missing_info" or missing:
            subject = str(entities.get("subject") or "goal")
            if "target_amount" in missing:
                follow_up = f"What price or target amount are you aiming for for the {subject}?"
                summary = "I can calculate the saving amount locally, but I need the target price first."
            elif "timeframe" in missing:
                follow_up = "How long do you want to give yourself to reach that target?"
                summary = "I have the target amount, but I need the timeframe to calculate what you should save."
            elif "periodic_contribution" in missing:
                follow_up = "How much are you planning to put away each month?"
                summary = "I can calculate how long the goal will take once I know your regular contribution."
            else:
                follow_up = "What target amount and timeframe do you want to use?"
                summary = "I need one more detail before I can calculate the goal."
            answer = cls._answer(
                title="I need one more detail",
                summary=summary,
                status="insufficient_information",
                needs_user_input=True,
                follow_up_question=follow_up,
            )
            return answer, base_data

        calculation = SmartLocalRouter.calculate_goal(entities)
        if not calculation:
            return None

        subject = str(entities.get("subject") or "goal")
        subject_label = subject.replace("_", " ")
        action = calculation["action"]

        if action == "required_contribution":
            target = calculation["target"]
            existing = calculation["existing"]
            remaining = calculation["remaining"]
            months = calculation["months"]
            monthly = calculation["monthly"]
            weekly = calculation["weekly"]
            yearly = calculation["yearly"]
            frequency = calculation["frequency"]
            primary_value = calculation["primary"]
            frequency_label = {"week": "week", "year": "year", "month": "month"}.get(frequency, "month")

            if remaining <= 0:
                summary = f"You already have enough set aside to cover the {cls._money(target)} target for your {subject_label}."
                status = "on_track"
            else:
                summary = (
                    f"To reach {cls._money(target)} for your {subject_label} in {months} months, "
                    f"you need to save about {cls._money(primary_value)} per {frequency_label}."
                )
                status = "on_track"

            metrics = [
                cls._metric("Target", cls._money(target)),
                cls._metric("Per month", cls._money(monthly)),
                cls._metric("Per year", cls._money(yearly)),
            ]
            if existing > 0:
                metrics.insert(1, cls._metric("Already set aside", cls._money(existing), sentiment="positive"))
                metrics = metrics[:4]
            else:
                metrics.append(cls._metric("Per week", cls._money(weekly)))

            next_step = f"Set a recurring transfer of about {cls._money(monthly)} each month." if remaining > 0 else None
            answer = cls._answer(
                title=f"{subject_label.title()} savings target",
                summary=summary,
                status=status,
                metrics=metrics,
                insights=[
                    cls._insight(
                        "Calculation",
                        f"{cls._money(remaining)} remaining ÷ {months} months = about {cls._money(monthly)} per month.",
                    )
                ],
                next_step=next_step,
            )
            return answer, base_data

        if action == "time_to_target":
            months = int(calculation["monthsNeeded"])
            years = months / 12 if months else 0
            contribution = calculation["contribution"]
            cadence = calculation["contributionFrequency"]
            target = calculation["target"]
            if months == 0:
                summary = f"You have already reached the {cls._money(target)} target."
            else:
                year_text = f" (about {years:.1f} years)" if months >= 12 else ""
                summary = (
                    f"At {cls._money(contribution)} per {cadence}, it would take about "
                    f"{months} months{year_text} to reach {cls._money(target)}."
                )
            answer = cls._answer(
                title="Time to savings goal",
                summary=summary,
                status="on_track",
                metrics=[
                    cls._metric("Target", cls._money(target)),
                    cls._metric("Time needed", f"{months} months"),
                    cls._metric("Contribution", f"{cls._money(contribution)} / {cadence}"),
                ],
            )
            return answer, base_data

        if action == "future_value":
            total = calculation["futureValue"]
            months = calculation["months"]
            contribution = calculation["contribution"]
            cadence = calculation["contributionFrequency"]
            answer = cls._answer(
                title="Projected savings",
                summary=(
                    f"Saving {cls._money(contribution)} per {cadence} for {months} months would build "
                    f"about {cls._money(total)}, before any interest or investment returns."
                ),
                status="neutral",
                metrics=[
                    cls._metric("Projected amount", cls._money(total)),
                    cls._metric("Time", f"{months} months"),
                ],
            )
            return answer, base_data

        return None

    @classmethod
    def _try_local_answer(
        cls,
        *,
        question: str,
        transactions: list[dict],
        settings: dict,
        coverage: dict,
        conversation_summary: dict[str, Any],
        route: RouteDecision,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        text = " ".join(question.split()).strip()
        lower = text.casefold()
        if not text:
            return None

        # The smart router is conservative: if it does not explicitly classify
        # this as a local factual question, do not guess from isolated keywords.
        if route.mode != "local":
            return None

        reports = ReportingService.build(transactions, settings, coverage)
        period_text = f"{lower} {str(route.entities.get('period') or '').casefold()}".strip()
        period, period_source = cls._select_period(period_text, reports, settings)
        category = str(route.entities.get("category") or "").strip() or cls._match_category(lower, transactions)
        merchant = str(route.entities.get("merchant") or "").strip() or cls._match_merchant(lower, transactions)

        base_data = {
            "intent": route.intent,
            "localFactType": None,
            "periodsIncluded": 1 if period else len(reports.get("monthly") or []),
            "monthsIncluded": 1 if period else len(reports.get("monthly") or []),
            "transactionDetailsIncluded": 0,
            "rawDescriptionsSent": False,
            "statementPdfSent": False,
            "accountOrCardNumbersSent": False,
            "contextSections": [],
            "periodResolvedFrom": period_source,
        }

        # Latest imported account balance.
        if route.intent == "balance" or re.search(r"\b(?:account\s+)?balance\b", lower) or "how much money do i have" in lower:
            latest = cls._latest_balance(transactions)
            if latest is None:
                return cls._missing_local("I can't see a usable balance", "Your imported transactions do not contain a balance value yet.", base_data, "balance")
            amount, day = latest
            answer = cls._answer(
                title="Latest imported balance",
                summary=f"Your latest imported account balance is {cls._money(amount)}.",
                status="neutral",
                metrics=[cls._metric("Balance", cls._money(amount))],
                insights=[cls._insight("As of", cls._date_label(day))],
                warnings=["This is the latest balance in your imported statement data, not your total net worth."],
            )
            base_data["localFactType"] = "balance"
            return answer, base_data

        # Largest confirmed expense.
        if route.intent == "largest_expense" or any(phrase in lower for phrase in ("largest expense", "biggest expense", "largest purchase", "biggest purchase", "most expensive purchase")):
            largest = cls._largest_expense(period, reports, transactions)
            if not largest:
                return cls._missing_local("No expense found", "I couldn't find a confirmed expense for that period.", base_data, "largest_expense")
            answer = cls._answer(
                title="Largest expense",
                summary=f"Your largest confirmed expense was {cls._money(largest['amount'])} at {largest['merchant']}.",
                status="neutral",
                metrics=[
                    cls._metric("Amount", cls._money(largest["amount"])),
                    cls._metric("Category", largest.get("category") or "Uncategorised"),
                ],
                insights=[cls._insight("Transaction", cls._transaction_label(largest))],
            )
            base_data.update({"localFactType": "largest_expense", "transactionDetailsIncluded": 1})
            return answer, base_data

        # Number of transactions.
        if route.intent == "transaction_count" or re.search(r"\bhow many\s+(?:transactions|purchases|expenses)\b", lower):
            items = cls._transactions_for_period(transactions, period)
            if "purchase" in lower or "expense" in lower:
                items = [item for item in items if item.get("financialRole") == "expense" and cls._num(item.get("amount")) < 0]
            count = len([item for item in items if item.get("transactionType") != "closing_balance" and item.get("financialRole") != "balance"])
            label = cls._period_name(period) if period else "your imported history"
            answer = cls._answer(
                title="Transaction count",
                summary=f"I found {count:,} matching transactions in {label}.",
                status="neutral",
                metrics=[cls._metric("Transactions", f"{count:,}")],
            )
            base_data["localFactType"] = "transaction_count"
            return answer, base_data

        # Category / merchant spend totals. Do this before generic total spending.
        if route.intent in {"category_spending", "merchant_spending"} or (cls._is_spend_total_question(lower) and (category or merchant)):
            items = cls._transactions_for_period(transactions, period)
            expenses = [
                item for item in items
                if item.get("financialRole") == "expense" and cls._num(item.get("amount")) < 0
            ]
            label = category or merchant or ""
            if category:
                expenses = [item for item in expenses if str(item.get("category") or "").casefold() == category.casefold()]
            if merchant:
                expenses = [item for item in expenses if str(item.get("displayName") or "").casefold() == merchant.casefold()]
            total = round(sum(abs(cls._num(item.get("amount"))) for item in expenses), 2)
            period_label = cls._period_name(period) if period else "your imported history"
            answer = cls._answer(
                title=f"{label} spending",
                summary=f"You spent {cls._money(total)} on {label} in {period_label}.",
                status="neutral",
                metrics=[
                    cls._metric("Spent", cls._money(total)),
                    cls._metric("Transactions", str(len(expenses))),
                ],
            )
            base_data.update({"localFactType": "category_or_merchant_spend", "transactionDetailsIncluded": 0})
            return answer, base_data

        # Total spending.
        if route.intent == "spending_total" or cls._is_spend_total_question(lower):
            total = cls._period_value(period, reports, "spending")
            label = cls._period_name(period) if period else "your imported history"
            answer = cls._answer(
                title="Total spending",
                summary=f"You spent {cls._money(total)} in {label}.",
                status="neutral",
                metrics=[cls._metric("Spent", cls._money(total))],
            )
            base_data["localFactType"] = "spending_total"
            return answer, base_data

        # Income / money in.
        if route.intent == "income_total" or cls._is_income_question(lower):
            total = cls._period_value(period, reports, "income")
            label = cls._period_name(period) if period else "your imported history"
            answer = cls._answer(
                title="Income received",
                summary=f"Confirmed income in {label} was {cls._money(total)}.",
                status="neutral",
                metrics=[cls._metric("Income", cls._money(total), sentiment="positive")],
                warnings=["Transfers, refunds and reimbursements are excluded from income."],
            )
            base_data["localFactType"] = "income_total"
            return answer, base_data

        # Net savings movement.
        if route.intent == "savings_total" or cls._is_savings_total_question(lower):
            total = cls._period_value(period, reports, "netSavingsMovement", savings=True)
            label = cls._period_name(period) if period else "your imported history"
            sentiment = "positive" if total > 0 else "negative" if total < 0 else "neutral"
            answer = cls._answer(
                title="Net savings",
                summary=f"Your net savings movement in {label} was {cls._money(total)}.",
                status="positive" if total > 0 else "attention" if total < 0 else "neutral",
                metrics=[cls._metric("Net saved", cls._money(total), sentiment=sentiment)],
                warnings=["WalletWise counts transfers into savings minus transfers back out."],
            )
            base_data["localFactType"] = "savings_total"
            return answer, base_data

        # Simple spending comparison: factual, not "why".
        if route.intent == "spending_comparison" or any(phrase in lower for phrase in ("did i spend more", "did i spend less", "spending higher", "spending lower", "spent more this", "spent less this")):
            comparison = cls._spending_comparison(reports, settings)
            if comparison is None:
                return cls._missing_local("Not enough comparable periods", "I need at least two comparable periods to answer that.", base_data, "spending_comparison")
            previous, current = comparison
            change = round(cls._num(current.get("spending")) - cls._num(previous.get("spending")), 2)
            pct = cls._pct_change(cls._num(previous.get("spending")), cls._num(current.get("spending")))
            if change > 0:
                summary = f"Yes. Spending increased by {cls._money(abs(change))} from {cls._period_name(previous)} to {cls._period_name(current)}."
                status = "spending_increased"
                sentiment = "negative"
            elif change < 0:
                summary = f"No. Spending decreased by {cls._money(abs(change))} from {cls._period_name(previous)} to {cls._period_name(current)}."
                status = "improving"
                sentiment = "positive"
            else:
                summary = f"Spending was unchanged between {cls._period_name(previous)} and {cls._period_name(current)}."
                status = "stable"
                sentiment = "neutral"
            change_text = f"{abs(pct):.1f}% {'increase' if change > 0 else 'decrease'}" if pct is not None and change != 0 else None
            answer = cls._answer(
                title="Spending comparison",
                summary=summary,
                status=status,
                metrics=[
                    cls._metric("Current", cls._money(current.get("spending")), sentiment=sentiment),
                    cls._metric("Previous", cls._money(previous.get("spending")), change=change_text),
                ],
            )
            base_data.update({"localFactType": "spending_comparison", "periodsIncluded": 2, "monthsIncluded": 2})
            return answer, base_data

        # "Where is my money going?" can be answered deterministically with top categories.
        if route.intent == "top_categories" or any(phrase in lower for phrase in ("where is my money going", "where did my money go", "top spending categories", "biggest categories")):
            source = period.get("categoryBreakdown") if period else reports.get("categoryBreakdown")
            rows = list(source or [])[:3]
            if not rows:
                return cls._missing_local("No spending categories yet", "I couldn't find categorised expenses in that period.", base_data, "top_categories")
            label = cls._period_name(period) if period else "your imported history"
            metrics = [cls._metric(str(row.get("name") or "Other"), cls._money(row.get("amount"))) for row in rows]
            answer = cls._answer(
                title="Where your money went",
                summary=f"These were your largest confirmed spending categories in {label}.",
                status="neutral",
                metrics=metrics,
            )
            base_data["localFactType"] = "top_categories"
            return answer, base_data

        # Most-used category / merchant by value.
        if route.intent == "top_category" or (("category" in lower and any(word in lower for word in ("most", "highest", "top", "largest"))) or "spend most on" in lower):
            source = period.get("categoryBreakdown") if period else reports.get("categoryBreakdown")
            top = (source or [None])[0]
            if top:
                answer = cls._answer(
                    title="Top spending category",
                    summary=f"{top['name']} was your largest spending category at {cls._money(top['amount'])}.",
                    status="neutral",
                    metrics=[cls._metric(top["name"], cls._money(top["amount"]))],
                )
                base_data["localFactType"] = "top_category"
                return answer, base_data

        if route.intent == "top_merchant" or ("merchant" in lower and any(word in lower for word in ("most", "highest", "top", "largest"))):
            source = period.get("merchantBreakdown") if period else reports.get("merchantBreakdown")
            top = (source or [None])[0]
            if top:
                answer = cls._answer(
                    title="Top merchant",
                    summary=f"{top['name']} was your highest-spend merchant at {cls._money(top['amount'])}.",
                    status="neutral",
                    metrics=[cls._metric(top["name"], cls._money(top["amount"]))],
                )
                base_data["localFactType"] = "top_merchant"
                return answer, base_data

        return None

    @classmethod
    def _select_period(cls, text: str, reports: dict, settings: dict) -> tuple[dict | None, str]:
        monthly = reports.get("monthly") or []
        pay_cycles = reports.get("payCycles") or []

        # Resolve an explicitly named month before generic words such as
        # "overall". "What did June cost me overall?" clearly means June.
        explicit_month = None
        for name, month_number in cls.MONTHS.items():
            if re.search(rf"\b{re.escape(name)}\b", text):
                explicit_month = month_number
                break
        if explicit_month is not None:
            year_match = re.search(r"\b(20\d{2})\b", text)
            year = int(year_match.group(1)) if year_match else None
            matches = []
            for item in monthly:
                try:
                    parsed = datetime.strptime(str(item.get("month")), "%Y-%m")
                except (TypeError, ValueError):
                    continue
                if parsed.month == explicit_month and (year is None or parsed.year == year):
                    matches.append(item)
            if matches:
                return matches[-1], "explicit_month"

        if any(phrase in text for phrase in ("all time", "entire history", "total ever")):
            return None, "all_history"
        # "overall" by itself is only all-history when no specific period was
        # resolved above.
        if "overall" in text:
            return None, "all_history"

        mode = str(settings.get("defaultTrackingView") or "calendar").casefold()
        # A question explicitly about calendar months must not silently use
        # salary pay cycles just because that is the user's default UI view.
        use_pay = ("month" not in text and mode in {"pay_cycle", "pay-cycle", "paycycle"}
                   and bool(pay_cycles))
        periods = pay_cycles if use_pay else monthly
        if not periods:
            return None, "none"

        if any(phrase in text for phrase in ("previous period", "previous month", "month before")):
            return (periods[-2] if len(periods) >= 2 else periods[-1]), "previous_period"

        if any(phrase in text for phrase in ("last month", "last pay cycle", "last cycle", "last complete period")):
            complete = [item for item in periods if cls._period_complete(item)]
            return (complete[-1] if complete else periods[-1]), "last_complete_period"

        if any(phrase in text for phrase in ("this month", "current month", "latest month", "this pay cycle", "current pay cycle", "latest period")):
            return periods[-1], "latest_period"

        complete = [item for item in periods if cls._period_complete(item)]
        return (complete[-1] if complete else periods[-1]), "latest_complete_period"

    @classmethod
    def _match_category(cls, text: str, transactions: list[dict]) -> str | None:
        categories = sorted({
            str(item.get("category")) for item in transactions
            if item.get("category") and item.get("category") != "Uncategorised"
        }, key=len, reverse=True)
        for category in categories:
            if category.casefold() in text:
                return category
        aliases = {
            "food": "Eating Out",
            "takeout": "Eating Out",
            "takeaways": "Eating Out",
            "groceries": "Groceries",
            "transport": "Transport",
            "shopping": "Shopping",
            "subscriptions": "Subscriptions",
            "entertainment": "Entertainment",
        }
        by_lower = {c.casefold(): c for c in categories}
        for alias, canonical in aliases.items():
            if re.search(rf"\b{alias}\b", text):
                return by_lower.get(canonical.casefold())
        return None

    @classmethod
    def _match_merchant(cls, text: str, transactions: list[dict]) -> str | None:
        merchants = sorted({
            str(item.get("displayName")).strip() for item in transactions if item.get("displayName")
        }, key=len, reverse=True)
        for merchant in merchants:
            if len(merchant) >= 3 and merchant.casefold() in text:
                return merchant
        return None

    @staticmethod
    def _is_spend_total_question(text: str) -> bool:
        return bool(
            re.search(r"\bhow much\b.*\b(?:spend|spent|spending)\b", text)
            or re.search(r"\bwhat did i spend\b", text)
            or re.search(r"\btotal spending\b", text)
        )

    @staticmethod
    def _is_income_question(text: str) -> bool:
        return bool(
            re.search(r"\bhow much\b.*\b(?:income|earn|earned|receive|received|came in)\b", text)
            or "total income" in text
            or "money came in" in text
        )

    @staticmethod
    def _is_savings_total_question(text: str) -> bool:
        # Historical savings only. Goal wording such as "how much do I need to
        # save R60,000 in 3 years" belongs to the goal calculator above.
        return SmartLocalRouter._is_savings_history(text)

    @classmethod
    def _period_value(cls, period: dict | None, reports: dict, key: str, *, savings: bool = False) -> float:
        if period is not None:
            return cls._num(period.get(key))
        if savings:
            return cls._num((reports.get("savings") or {}).get(key))
        return cls._num((reports.get("summary") or {}).get(key))

    @classmethod
    def _largest_expense(cls, period: dict | None, reports: dict, transactions: list[dict]) -> dict | None:
        items = cls._transactions_for_period(transactions, period)
        expenses = [
            item for item in items
            if item.get("financialRole") == "expense" and cls._num(item.get("amount")) < 0
        ]
        if not expenses:
            return None
        item = max(expenses, key=lambda row: abs(cls._num(row.get("amount"))))
        return {
            "amount": abs(cls._num(item.get("amount"))),
            "merchant": item.get("displayName") or "Unknown merchant",
            "category": item.get("category") or "Uncategorised",
            "date": item.get("date"),
        }

    @classmethod
    def _transactions_for_period(cls, transactions: list[dict], period: dict | None) -> list[dict]:
        if period is None:
            return list(transactions)
        start = cls._parse_date(period.get("startDate"))
        end = cls._parse_date(period.get("endDate"))
        month = period.get("month")
        selected = []
        for item in transactions:
            day = cls._parse_date(item.get("date"))
            if day is None:
                continue
            if start and end and start <= day <= end:
                selected.append(item)
            elif month and day.strftime("%Y-%m") == str(month):
                selected.append(item)
        return selected

    @classmethod
    def _spending_comparison(cls, reports: dict, settings: dict) -> tuple[dict, dict] | None:
        mode = str(settings.get("defaultTrackingView") or "calendar").casefold()
        pay = reports.get("payCycles") or []
        periods = pay if mode in {"pay_cycle", "pay-cycle", "paycycle"} and pay else (reports.get("monthly") or [])
        complete = [item for item in periods if cls._period_complete(item)]
        candidates = complete if len(complete) >= 2 else periods
        if len(candidates) < 2:
            return None
        return candidates[-2], candidates[-1]

    @classmethod
    def _latest_balance(cls, transactions: list[dict]) -> tuple[float, date] | None:
        rows = []
        for item in transactions:
            if item.get("balance") is None:
                continue
            day = cls._parse_date(item.get("date"))
            if day is None:
                continue
            rows.append((day, cls._num(item.get("balance"))))
        if not rows:
            return None
        day, amount = max(rows, key=lambda row: row[0])
        return amount, day

    @staticmethod
    def _period_complete(period: dict) -> bool:
        if "complete" in period:
            return bool(period.get("complete"))
        return period.get("coverageStatus") != "partial"

    @staticmethod
    def _period_name(period: dict | None) -> str:
        if not period:
            return "your imported history"
        return str(period.get("label") or period.get("month") or period.get("id") or "the selected period")

    @staticmethod
    def _money(value: Any) -> str:
        try:
            amount = float(value or 0)
        except (TypeError, ValueError):
            amount = 0.0
        return f"R{amount:,.2f}"

    @staticmethod
    def _num(value: Any) -> float:
        try:
            return round(float(value or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _pct_change(previous: float, current: float) -> float | None:
        if previous == 0:
            return None if current != 0 else 0.0
        return round(((current - previous) / previous) * 100, 1)

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if not value:
            return None
        if isinstance(value, date):
            return value
        text = str(value).strip()
        try:
            return date.fromisoformat(text[:10])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _date_label(value: date | str | None) -> str:
        if isinstance(value, date):
            return value.strftime("%d %b %Y")
        try:
            return date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")
        except (TypeError, ValueError):
            return str(value or "Unknown date")

    @classmethod
    def _transaction_label(cls, item: dict) -> str:
        return f"{item.get('merchant') or 'Unknown merchant'} · {cls._date_label(item.get('date'))} · {item.get('category') or 'Uncategorised'}"

    @staticmethod
    def _metric(label: str, value: str, change: str | None = None, sentiment: str = "neutral") -> dict:
        return {"label": label, "value": value, "change": change, "sentiment": sentiment}

    @staticmethod
    def _insight(title: str, text: str, sentiment: str = "neutral") -> dict:
        return {"title": title, "text": text, "sentiment": sentiment}

    @classmethod
    def _answer(
        cls,
        *,
        title: str,
        summary: str,
        status: str,
        metrics: list[dict] | None = None,
        insights: list[dict] | None = None,
        recommendations: list[dict] | None = None,
        warnings: list[str] | None = None,
        next_step: str | None = None,
        needs_user_input: bool = False,
        follow_up_question: str | None = None,
    ) -> dict[str, Any]:
        return {
            "title": title,
            "summary": summary,
            "status": status,
            "key_metrics": (metrics or [])[:4],
            "insights": (insights or [])[:3],
            "recommendations": (recommendations or [])[:3],
            "warnings": (warnings or [])[:2],
            "next_step": next_step,
            "needs_user_input": needs_user_input,
            "follow_up_question": follow_up_question,
        }

    @classmethod
    def _missing_local(cls, title: str, summary: str, data_used: dict, fact_type: str):
        data_used["localFactType"] = fact_type
        return cls._answer(title=title, summary=summary, status="insufficient_information"), data_used

    @classmethod
    def _debug_local(cls, question: str, answer: dict[str, Any], data_used: dict[str, Any], trace_id: str = "--------") -> None:
        # Legacy local-answer debug removed. The unified semantic-routing trace
        # now contains planning, validation, execution and final-answer details.
        return


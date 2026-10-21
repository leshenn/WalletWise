"""Regression tests for Wally's routing and evidence boundaries.

Pure synthetic data; no Groq credentials, personal statements or network calls.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.ai_chat_repository import AIChatRepository
from app.ai_context import AIContextBuilder
from app.ai_service import AIServiceResult
from app.wally_engine import WallyAnswerEngine
from app.wally_query_plan import DeterministicQueryPlanner

ROWS = [
    {"id": "jun-food", "date": "2026-06-04", "amount": -600, "financialRole": "expense", "category": "Groceries", "displayName": "Checkers", "transactionType": "debit", "bankCharges": 2},
    {"id": "jun-bank", "date": "2026-06-05", "amount": -20, "financialRole": "expense", "category": "Bank Fees", "displayName": "Bank", "transactionType": "debit", "bankCharges": 20},
    {"id": "jun-pay", "date": "2026-06-23", "amount": 4000, "financialRole": "income", "category": "Salary", "displayName": "Employer", "transactionType": "credit"},
    {"id": "jul-food", "date": "2026-07-04", "amount": -400, "financialRole": "expense", "category": "Groceries", "displayName": "Checkers", "transactionType": "debit", "bankCharges": 3},
    {"id": "jul-bank", "date": "2026-07-05", "amount": -35, "financialRole": "expense", "category": "Bank Fees", "displayName": "Bank", "transactionType": "debit"},
    {"id": "jul-pay", "date": "2026-07-23", "amount": 4100, "financialRole": "income", "category": "Salary", "displayName": "Employer", "transactionType": "credit"},
    {"id": "aug-food", "date": "2026-08-04", "amount": -300, "financialRole": "expense", "category": "Groceries", "displayName": "Checkers", "transactionType": "debit", "bankCharges": 5},
    {"id": "aug-bank", "date": "2026-08-05", "amount": -40, "financialRole": "expense", "category": "Bank Fees", "displayName": "Bank", "transactionType": "debit"},
    {"id": "aug-pay", "date": "2026-08-23", "amount": 4200, "financialRole": "income", "category": "Salary", "displayName": "Employer", "transactionType": "credit"},
]
COVERAGE = {"months": [{"month": f"2026-{month:02d}", "status": "covered", "coveragePct": 100}
                       for month in (6, 7, 8)]}


class WallyConsistencyTests(unittest.TestCase):
    def setUp(self):
        os.environ["WALLETWISE_TRACE"] = "false"

    def answer(self, question, **kwargs):
        return WallyAnswerEngine.answer(question=question, transactions=ROWS,
                                        settings={"defaultTrackingView": "calendar"},
                                        coverage=COVERAGE, trace_id="CONSISTENCY", **kwargs)

    def test_bank_fee_sum_is_not_total_spend(self):
        actual = self.answer("How much did I spend on bank fees?")
        self.assertEqual(actual.data_used["answerMode"], "local")
        self.assertEqual(actual.data_used["categoryFilter"], "Bank Fees")
        self.assertIn("R105.00", actual.answer["summary"])

    def test_bank_fee_sum_counts_column_once(self):
        actual = self.answer("What were my bank charges in June 2026?")
        self.assertIn("R22.00", actual.answer["summary"])
        self.assertEqual(actual.data_used["periodsIncluded"], 1)

    def test_groceries_for_one_month(self):
        actual = self.answer("How much did I spend on groceries in July 2026?")
        self.assertIn("R400.00", actual.answer["summary"])
        self.assertEqual(actual.data_used["categoryFilter"], "Groceries")

    def test_unavailable_month_never_substitutes_august(self):
        actual = self.answer("How much did I spend in February 2020?")
        self.assertEqual(actual.answer["status"], "insufficient_information")
        self.assertIn("February 2020", actual.answer["summary"])
        self.assertNotIn("R", actual.answer["summary"])
        self.assertEqual(actual.data_used["periodResolvedFrom"], "not_found")

    def test_missing_month_for_rank_is_not_replaced_by_imported_history(self):
        actual = self.answer("What was my largest spending category in May 2025?")
        self.assertEqual(actual.answer["status"], "insufficient_information")
        self.assertIn("May 2025", actual.answer["summary"])

    def test_total_income_is_all_imported_history(self):
        plan = DeterministicQueryPlanner.build("What is my total income?", {})
        self.assertEqual(plan.scope, "all_history")
        actual = self.answer("What is my total income?")
        self.assertIn("R12,300.00", actual.answer["summary"])

    def test_multi_month_total_is_not_single_month_local(self):
        plan = DeterministicQueryPlanner.build("What was my total spending over the past three months?", {})
        self.assertEqual(plan.route, "ai")

    def test_income_minus_spending_goes_to_reasoning(self):
        plan = DeterministicQueryPlanner.build("How much of my salary do I typically have left after spending?", {})
        self.assertEqual(plan.route, "ai")

    def test_two_subjects_must_not_use_category_sum(self):
        plan = DeterministicQueryPlanner.build("Do I spend more on food or transport, and by how much?", {})
        self.assertEqual(plan.route, "ai")

    def test_comparing_fee_growth_vs_spend_requires_reasoning(self):
        plan = DeterministicQueryPlanner.build("Are my bank fees increasing faster than my overall spending?", {})
        self.assertEqual(plan.route, "ai")
        self.assertEqual(plan.entities.get("category"), "Bank Fees")

    def test_multicategory_trend_not_overall_trend(self):
        plan = DeterministicQueryPlanner.build("Which categories contributed most to my spending increase last month?", {})
        self.assertEqual(plan.route, "ai")

    def test_percentage_does_not_use_category_rank(self):
        plan = DeterministicQueryPlanner.build("Which category takes up the largest percentage of my income?", {})
        self.assertEqual(plan.route, "ai")

    def test_plain_single_transaction_remains_local(self):
        plan = DeterministicQueryPlanner.build("What was my biggest individual expense?", {})
        self.assertEqual(plan.route, "local")
        self.assertEqual(plan.operation, "max_transaction")

    def test_followup_month_retains_prior_category(self):
        original = AIChatRepository._extract_structured_state("How much did I spend on groceries in July 2026?")
        self.assertEqual(original["category"], "Groceries")
        self.assertEqual(original["period"], "july 2026")
        self.assertEqual(original["operation"], "sum")
        plan = DeterministicQueryPlanner.build("What about June 2026?", {"state": original})
        self.assertEqual(plan.route, "local")
        self.assertEqual(plan.entities["category"], "Groceries")
        self.assertEqual(plan.period, "june 2026")
        actual = self.answer("What about June 2026?", conversation_summary={"state": original})
        self.assertIn("R600.00", actual.answer["summary"])

    def test_new_standalone_question_ignores_old_category(self):
        state = {"state": {"category": "Groceries", "groupBy": "category", "measure": "spending", "operation": "sum", "period": "july 2026"}}
        plan = DeterministicQueryPlanner.build("How much did I spend in August 2026?", state)
        self.assertEqual(plan.group_by, "none")
        self.assertNotEqual(plan.entities.get("category"), "Groceries")

    def test_verified_evidence_contains_monthly_category_values(self):
        context, info = AIContextBuilder.build(
            question="Why have my fees changed compared with groceries?", transactions=ROWS,
            settings={"defaultTrackingView": "calendar"}, coverage=COVERAGE,
            intent_override="spending_analysis",
            entity_hints={"category": "Bank Fees"},
        )
        monthly = context["verifiedEvidence"]["monthly"]
        self.assertEqual(len(monthly), 3)
        self.assertEqual(monthly[1]["categories"]["Groceries"], 400.0)
        self.assertEqual(monthly[1]["categories"]["Bank Fees"], 35.0)
        self.assertEqual(monthly[1]["extraBankChargeFields"], 3.0)
        self.assertEqual(info["rawDescriptionsSent"], False)

    @patch("app.wally_engine.GroqAIService.ask")
    def test_complex_answer_uses_ai_with_verified_evidence(self, ask):
        ask.return_value = AIServiceResult(
            answer={"title": "Supported analysis", "summary": "The verified data requires comparison.",
                    "status": "neutral", "key_metrics": [], "insights": [], "recommendations": [],
                    "warnings": [], "next_step": None, "needs_user_input": False,
                    "follow_up_question": None},
            usage={"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550},
            model="mock", provider_payload={}, raw_answer="{}",
        )
        result = self.answer("Are my bank fees increasing faster than my overall spending?")
        self.assertTrue(result.ai_used)
        self.assertEqual(result.data_used["answerMode"], "ai")
        self.assertIn("verifiedEvidence", ask.call_args.kwargs["context"])
        self.assertEqual(ask.call_args.kwargs["context"]["request"]["category"], "Bank Fees")


if __name__ == "__main__":
    unittest.main()

# Regression: selection direction must be preserved.
class SmallestExpenseRegression(unittest.TestCase):
    def test_smallest_expense(self):
        os.environ["WALLETWISE_TRACE"] = "false"
        plan = DeterministicQueryPlanner.build("What was my smallest expense?", {})
        self.assertEqual(plan.operation, "min_transaction")
        result = WallyAnswerEngine.answer(question="What was my smallest expense?",
                                           transactions=ROWS, coverage=COVERAGE,
                                           settings={"defaultTrackingView": "calendar"})
        self.assertEqual(result.data_used["localFactType"], "smallest_expense")
        self.assertIn("R20.00", result.answer["summary"])

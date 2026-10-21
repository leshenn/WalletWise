import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.ai_service import AIServiceResult
from app.ai_chat_repository import AIChatRepository
from app.wally_engine import WallyAnswerEngine
from app.wally_query_plan import DeterministicQueryPlanner, QueryPlan, QueryPlanValidator
from app.wally_query_planner import AIQueryPlanResult
from app.wally_router import SmartLocalRouter


TRANSACTIONS = [
    {"id": "may-food", "date": "2026-05-10", "amount": -1000.0, "financialRole": "expense", "category": "Groceries", "displayName": "Checkers", "transactionType": "debit", "balance": 9000.0, "bank": "FNB"},
    {"id": "may-travel", "date": "2026-05-12", "amount": -500.0, "financialRole": "expense", "category": "Travel", "displayName": "Uber", "transactionType": "debit", "balance": 8500.0, "bank": "FNB"},
    {"id": "jun-food", "date": "2026-06-10", "amount": -800.0, "financialRole": "expense", "category": "Groceries", "displayName": "Checkers", "transactionType": "debit", "balance": 7700.0, "bank": "FNB"},
    {"id": "jun-travel", "date": "2026-06-20", "amount": -1800.0, "financialRole": "expense", "category": "Travel", "displayName": "FlySafair", "transactionType": "debit", "balance": 5900.0, "bank": "FNB"},
    {"id": "jun-pay", "date": "2026-06-25", "amount": 5000.0, "financialRole": "income", "category": "Salary", "displayName": "Salary", "transactionType": "credit", "balance": 10900.0, "bank": "FNB", "isSalary": True},
    {"id": "jul-travel", "date": "2026-07-23", "amount": -2076.32, "financialRole": "expense", "category": "Travel", "displayName": "FlySafair", "transactionType": "debit", "balance": 8800.0, "bank": "FNB"},
    {"id": "jul-eat", "date": "2026-07-25", "amount": -500.0, "financialRole": "expense", "category": "Eating Out", "displayName": "Restaurant", "transactionType": "debit", "balance": 8300.0, "bank": "FNB"},
]

AI_ANSWER = {
    "title": "Purchase pressure",
    "summary": "This requires an affordability judgement.",
    "status": "attention",
    "key_metrics": [],
    "insights": [],
    "recommendations": [],
    "warnings": [],
    "next_step": None,
    "needs_user_input": False,
    "follow_up_question": None,
}


class SemanticRoutingTests(unittest.TestCase):
    def setUp(self):
        os.environ["WALLETWISE_TRACE"] = "false"

    def test_past_months_biggest_expense_category_is_category_ranking(self):
        question = "For the past months which category has been my biggest expense?"
        plan = DeterministicQueryPlanner.build(question, {})
        self.assertEqual(plan.route, "local")
        self.assertEqual(plan.query_type, "aggregate")
        self.assertEqual(plan.operation, "rank")
        self.assertEqual(plan.group_by, "category")
        self.assertEqual(plan.scope, "past_complete_periods")
        self.assertTrue(QueryPlanValidator.validate(question, plan).valid)

        result = WallyAnswerEngine.answer(
            question=question,
            transactions=TRANSACTIONS,
            settings={"defaultTrackingView": "calendar"},
            coverage={},
            trace_id="TEST1001",
        )
        self.assertEqual(result.data_used["localFactType"], "category_ranking")
        self.assertEqual(result.data_used["answerMode"], "local")
        self.assertIn("Travel", result.answer["summary"])
        self.assertIn("complete months", result.answer["summary"])
        self.assertGreaterEqual(result.data_used["periodsIncluded"], 2)

    def test_biggest_expense_without_category_is_single_transaction(self):
        plan = DeterministicQueryPlanner.build("What was my biggest expense?", {})
        self.assertEqual(plan.query_type, "transaction")
        self.assertEqual(plan.operation, "max_transaction")
        self.assertEqual(plan.group_by, "none")

    def test_biggest_spend_category_defaults_to_history_not_latest_month(self):
        plan = DeterministicQueryPlanner.build("what is my biggest spend category", {})
        self.assertEqual(plan.group_by, "category")
        self.assertEqual(plan.scope, "all_history")

    def test_goal_calculation_stays_local(self):
        result = WallyAnswerEngine.answer(
            question="How much do I need to save to buy a car that is 60000 in 3 years?",
            transactions=[],
            trace_id="TEST1002",
        )
        self.assertFalse(result.ai_used)
        self.assertEqual(result.data_used["answerMode"], "local")
        self.assertIn("R1,666.67", result.answer["summary"])

    def test_affordability_pressure_is_ai(self):
        plan = DeterministicQueryPlanner.build(
            "Would buying a phone that is 33000 put me under financial pressure?", {}
        )
        self.assertEqual(plan.route, "ai")
        self.assertEqual(plan.ai_intent, "affordability")
        self.assertEqual(plan.entities.get("purchasePrice"), 33000.0)

    def test_answer_validator_rejects_transaction_for_category_plan(self):
        plan = QueryPlan(
            route="local", query_type="aggregate", measure="spending", operation="rank",
            group_by="category", scope="all_history", direction="highest", limit=5,
            confidence=0.9, reason="test",
        )
        validation = QueryPlanValidator.validate_answer(plan, {"localFactType": "largest_expense"})
        self.assertFalse(validation.valid)

    def test_old_chat_memory_cannot_override_complete_new_question(self):
        old_memory = {
            "memoryVersion": 1,
            "state": {"intent": "saving_goal", "subject": "phone", "targetAmount": 1500, "timeframeMonths": 36},
        }
        question = "Would it be a good financial decision to take a R1500 contract for a new phone now?"
        plan = DeterministicQueryPlanner.build(question, old_memory)
        self.assertEqual(plan.route, "ai")
        self.assertEqual(plan.ai_intent, "affordability")
        self.assertEqual(plan.entities.get("recurringPayment"), 1500.0)
        self.assertFalse(SmartLocalRouter.is_contextual_followup(question))

    def test_saved_chat_title_is_short(self):
        title = AIChatRepository._fallback_title_from_question(
            "Would it be a good financial decision to take a R1500 contract for a new phone now?"
        )
        self.assertLessEqual(len(title), AIChatRepository.TITLE_LIMIT)

    @patch("app.wally_engine.WallyAIQueryPlanner.plan")
    def test_ambiguous_language_can_plan_then_execute_locally(self, planner):
        ai_plan = QueryPlan(
            route="local", query_type="aggregate", measure="spending", operation="sum",
            group_by="none", scope="specific_period", period="June 2026",
            confidence=0.97, reason="The user asks for June's overall spend.", source="ai_query_planner",
        )
        planner.return_value = AIQueryPlanResult(
            plan=ai_plan,
            model="router-test",
            usage={"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
            raw_response='{"route":"local"}',
            provider_payload={"model": "router-test"},
            request_body={"q": "What did June cost me overall?", "memory": {}},
        )
        result = WallyAnswerEngine.answer(
            question="What did June cost me overall?",
            transactions=TRANSACTIONS,
            settings={"defaultTrackingView": "calendar"},
            coverage={},
            trace_id="TEST1003",
        )
        self.assertEqual(result.data_used["answerMode"], "local")
        self.assertTrue(result.data_used["queryPlannerUsed"])
        self.assertEqual(result.data_used["queryPlannerTotalTokens"], 160)
        self.assertFalse(result.data_used["financialDataSentExternally"])

    @patch("app.wally_engine.GroqAIService.ask")
    def test_ai_path_receives_affordability_context_only_after_plan(self, ask):
        captured = {}
        def fake_ask(**kwargs):
            captured.update(kwargs)
            return AIServiceResult(
                answer=AI_ANSWER,
                usage={"prompt_tokens": 450, "completion_tokens": 90, "total_tokens": 540},
                model="answer-test",
                provider_payload={"model": "answer-test", "messages": []},
                raw_answer='{"title":"Purchase pressure"}',
            )
        ask.side_effect = fake_ask
        result = WallyAnswerEngine.answer(
            question="Would buying a phone that is 33000 put me under financial pressure?",
            transactions=TRANSACTIONS,
            settings={"defaultTrackingView": "calendar"},
            coverage={},
            trace_id="TEST1004",
        )
        self.assertEqual(captured["context"]["intent"], "affordability")
        self.assertEqual(captured["context"]["request"]["purchasePrice"], 33000.0)
        self.assertEqual(result.data_used["answerTotalTokens"], 540)


if __name__ == "__main__":
    unittest.main()

# This suite intentionally uses invented amounts, never the uploaded database.
class MonthlyTrendRegressionTests(unittest.TestCase):
    def setUp(self):
        os.environ["WALLETWISE_TRACE"] = "false"
        self.coverage = {"months": [
            {"month": "2026-05", "status": "covered", "coveragePct": 100},
            {"month": "2026-06", "status": "covered", "coveragePct": 100},
            {"month": "2026-07", "status": "covered", "coveragePct": 100},
        ]}
        self.transactions = [
            {"id": "m-fees", "date": "2026-05-04", "amount": -30, "financialRole": "expense", "category": "Bank Fees", "displayName": "Account Fee", "transactionType": "debit"},
            {"id": "j-fees", "date": "2026-06-04", "amount": -40, "financialRole": "expense", "category": "Bank Fees", "displayName": "Account Fee", "transactionType": "debit"},
            {"id": "l-fees", "date": "2026-07-04", "amount": -50, "financialRole": "expense", "category": "Bank Fees", "displayName": "Account Fee", "transactionType": "debit"},
            # Overall spending DECREASES but fees INCREASE. The other purchase
            # has an additional per-transaction bank fee in a separate column.
            {"id": "m-food", "date": "2026-05-07", "amount": -1000, "financialRole": "expense", "category": "Groceries", "displayName": "Supermarket", "transactionType": "debit", "bankCharges": 3},
            {"id": "j-food", "date": "2026-06-07", "amount": -200, "financialRole": "expense", "category": "Groceries", "displayName": "Supermarket", "transactionType": "debit", "bankCharges": 3},
            {"id": "l-food", "date": "2026-07-07", "amount": -100, "financialRole": "expense", "category": "Groceries", "displayName": "Supermarket", "transactionType": "debit", "bankCharges": 3},
        ]

    def answer(self, question, *, coverage=None, transactions=None):
        return WallyAnswerEngine.answer(
            question=question,
            transactions=self.transactions if transactions is None else transactions,
            coverage=self.coverage if coverage is None else coverage,
            settings={"defaultTrackingView": "pay_cycle"},  # Explicit 'months' beats pay cycles
            trace_id="TRENDTEST",
        )

    @patch("app.wally_engine.GroqAIService.ask")
    @patch("app.wally_engine.WallyAIQueryPlanner.plan")
    def test_bank_fees_increase_but_spending_decreases(self, query_planner, ai_ask):
        q = "Has my bank fees been increasing over the past few months?"
        plan = DeterministicQueryPlanner.build(q, {})
        self.assertEqual(plan.entities.get("category"), "Bank Fees")
        self.assertEqual(plan.group_by, "category")
        self.assertEqual(plan.scope, "past_complete_periods")
        self.assertEqual(plan.operation, "compare")
        self.assertTrue(QueryPlanValidator.validate(q, plan).valid)
        result = self.answer(q)
        self.assertFalse(result.ai_used)
        self.assertEqual(result.data_used["localFactType"], "category_trend")
        self.assertEqual(result.data_used["categoryFilter"], "Bank Fees")
        self.assertEqual(result.data_used["periodsIncluded"], 3)
        self.assertEqual([p["amount"] for p in result.data_used["monthlyBreakdown"]], [33, 43, 53])
        self.assertEqual(result.data_used["trend"], "increasing")
        self.assertIn("Yes.", result.answer["summary"])
        self.assertNotIn("overall spending", result.answer["summary"].lower())
        query_planner.assert_not_called()
        ai_ask.assert_not_called()

    def test_fluctuation_is_not_mislabeled_increasing(self):
        rows = [dict(t) for t in self.transactions]
        rows[2]["amount"] = -20  # 30 -> 40 -> 20, not increasing month-by-month
        result = self.answer("Have bank charges increased over the past months?", transactions=rows)
        self.assertEqual(result.data_used["trend"], "fluctuating")
        self.assertIn("Not consistently", result.answer["summary"])

    def test_partial_coverage_not_treated_as_zero_or_complete(self):
        coverage = {"months": [dict(m) for m in self.coverage["months"]]}
        coverage["months"][1]["status"] = "partial"
        coverage["months"][1]["coveragePct"] = 30
        result = self.answer("Are my bank fees going up over the past few months?", coverage=coverage)
        self.assertEqual(result.answer["status"], "insufficient_information")
        self.assertNotIn("Yes.", result.answer["summary"])
        self.assertNotIn("No.", result.answer["summary"])

    def test_category_fees_row_is_not_double_counted(self):
        rows = [dict(t) for t in self.transactions]
        rows[0]["bankCharges"] = 30  # Possible duplicated amount, must not double count
        result = self.answer("Have my account fees been increasing recently?", transactions=rows)
        self.assertEqual(result.data_used["monthlyBreakdown"][0]["amount"], 33)

    def test_explicit_month_count_persists_in_plan(self):
        plan = DeterministicQueryPlanner.build("Have my grocery expenses increased over the last 6 months?", {})
        self.assertEqual(plan.entities["category"], "Groceries")
        self.assertEqual(plan.entities["periodCount"], 6)
        self.assertEqual(plan.group_by, "category")

    def test_mismatched_subject_from_ai_query_plan_is_rejected(self):
        question = "Have my bank charges been increasing over recent months?"
        bad = QueryPlan(
            route="local", query_type="comparison", measure="spending", operation="compare",
            group_by="period", scope="past_complete_periods", entities={}, confidence=.99,
        )
        validation = QueryPlanValidator.validate(question, bad)
        self.assertFalse(validation.valid)
        self.assertTrue(any("Bank Fees" in issue for issue in validation.issues))

    def test_no_recognized_fee_data_is_not_zero_fee_claim(self):
        rows = [dict(t) for t in self.transactions if t["category"] != "Bank Fees"]
        for row in rows:
            row["bankCharges"] = None
        result = self.answer("Have my banking fees risen recently?", transactions=rows)
        self.assertEqual(result.answer["status"], "insufficient_information")
        self.assertIn("does not prove", result.answer["summary"])

    def test_generic_spending_trend_is_separate_from_fee_trend(self):
        result = self.answer("Has my total spending been decreasing over the last few months?")
        self.assertEqual(result.data_used["categoryFilter"], None)
        self.assertEqual(result.data_used["trend"], "decreasing")
        self.assertIn("Overall spending", result.answer["summary"])

"""Capitec bank adapter and bank-scope integration tests.

No real account statements or private information ship with this repository.
The optional full PDF test runs only if WALLETWISE_CAPITEC_TEST_PDF is supplied.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.database import Database
from app.repositories import (DatasetRepository, NamingRuleRepository,
                              CategoryRuleRepository, SalarySourceRepository)
from app.services import StatementProcessingService
from app.reporting import ReportingService
from walletwise.config import AppConfig
from walletwise.extraction.bank_detector import BankDetector
from walletwise.extraction.registry import ExtractorRegistry
from walletwise.models import Transaction


class CapitecMultiBankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Database(Path(self.tmp.name) / "walletwise.db")
        self.repo = DatasetRepository(self.database)

    def add(self, bank, date, amount, description, balance):
        tx = Transaction(
            date=datetime.fromisoformat(date), raw_description=description,
            amount=amount, balance=balance, bank=bank,
            financial_role="income" if amount > 0 else "expense",
            category="Income" if amount > 0 else "Groceries",
            transaction_type="credit" if amount > 0 else "debit",
        )
        self.repo.append_statement(statement_id=f"stmt-{bank.lower()}-{date}",
                                   filename=f"{bank}.pdf", bank=bank,
                                   file_hash=f"sha-{bank}-{date}",
                                   transactions=[(f"tx-{bank}-{date}", tx)],
                                   statement_period={"verified":True,"start":date,"end":date,"source":"test"})

    def test_bank_isolation_and_coverage(self):
        self.add("FNB", "2026-01-05", -100, "FNB purchase", 900)
        self.add("Capitec", "2025-12-05", -50, "Capitec purchase", 450)
        self.assertEqual(len(self.repo.list_transactions()), 2)
        self.assertEqual(len(self.repo.list_transactions("FNB")), 1)
        self.assertEqual(len(self.repo.list_transactions("Capitec")), 1)
        self.assertEqual(self.repo.coverage("FNB")["start"], "2026-01-05")
        self.assertEqual(self.repo.coverage("Capitec")["start"], "2025-12-05")
        self.assertEqual(self.repo.list_transactions("FNB")[0]["bank"], "FNB")
        self.assertEqual(self.repo.list_transactions("Capitec")[0]["bank"], "Capitec")

    def test_identical_posted_fee_rows_are_not_lost(self):
        rows = [
            Transaction(date=datetime(2025,6,5), raw_description="Capitec bank fee: PayShap", amount=-6,
                        balance=24, bank="Capitec", category="Bank Fees", transaction_type="debit")
            for _ in range(3)
        ]
        first = self.repo.append_statement(statement_id="one", filename="cap.pdf", bank="Capitec",
                     file_hash="hash-1", transactions=[(f"a-{i}",t) for i,t in enumerate(rows)])
        self.assertEqual(first["importedCount"], 3)
        second = self.repo.append_statement(statement_id="two", filename="cap-overlap.pdf", bank="Capitec",
                     file_hash="hash-2", transactions=[(f"b-{i}",t) for i,t in enumerate(rows)])
        self.assertEqual(second["importedCount"], 0)
        self.assertEqual(second["duplicateCount"], 3)
        self.assertEqual(len(self.repo.list_transactions("Capitec")), 3)

    def test_registry_and_invalid_bank(self):
        self.assertEqual(set(ExtractorRegistry.SUPPORTED_BANKS), {"FNB", "Capitec"})
        self.assertEqual(ExtractorRegistry.create("capitec", AppConfig()).bank_name, "Capitec")
        from walletwise.exceptions import UnsupportedBankError
        with self.assertRaises(UnsupportedBankError):
            ExtractorRegistry.create("SomeOtherBank", AppConfig())

    def test_real_capitec_pdf_optional(self):
        pdf_file = os.getenv("WALLETWISE_CAPITEC_TEST_PDF")
        if not pdf_file or not Path(pdf_file).is_file():
            self.skipTest("Set WALLETWISE_CAPITEC_TEST_PDF to test against a private local PDF")
        data = Path(pdf_file).read_bytes()
        self.assertEqual(BankDetector().detect(pdf_file), "Capitec")
        service = StatementProcessingService(
            dataset_repository=self.repo, rule_repository=NamingRuleRepository(self.database),
            category_rule_repository=CategoryRuleRepository(self.database),
            salary_source_repository=SalarySourceRepository(self.database),
            data_directory=self.tmp.name)
        result = service.process_pdf_bytes(filename="capitec.pdf", contents=data)
        txs = self.repo.list_transactions("Capitec")
        self.assertEqual(result["importedCount"], len(txs))
        self.assertTrue(result["periodDetection"]["verified"])
        # Verify this particular sample against its independently observed
        # statement totals. Other user-supplied Capitec PDFs may differ.
        if Path(pdf_file).name == "capitec_2.pdf":
            self.assertAlmostEqual(sum(t["amount"] for t in txs if t["category"] == "Bank Fees"), -1363.95, places=2)
            self.assertEqual(len(txs), 1300)
        self.assertTrue(any(t["savingsDirection"] == "into" for t in txs))
        self.assertTrue(any(t["savingsDirection"] == "out" for t in txs))

    def test_no_cross_bank_deduplication(self):
        a = Transaction(date=datetime(2026,1,1), raw_description="Same merchant", amount=-10,
                        balance=80, transaction_type="debit")
        for i, bank in enumerate(("FNB", "Capitec")):
            self.repo.append_statement(statement_id=f"st-{i}", filename=f"{bank}.pdf", bank=bank,
                           file_hash=f"unique-{i}", transactions=[(f"tx-{i}",a)])
        self.assertEqual(len(self.repo.list_transactions()),2)


if __name__ == "__main__":
    unittest.main()

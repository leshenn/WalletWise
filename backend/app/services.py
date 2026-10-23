from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

from walletwise import AppConfig
from walletwise.extraction import BankDetector, ExtractorRegistry
from walletwise.processing import (
    CategoryService,
    ConfidenceService,
    FinancialRoleService,
    TransactionNamer,
    TransactionValidator,
)

from .salary_detection import SalaryDetectionService
from .repositories import (
    CategoryRuleRepository,
    DatasetRepository,
    NamingRuleRepository,
    SalarySourceRepository,
)


class DatabaseAwareTransactionNamer:
    """Database naming rules first, then generic naming heuristics."""

    def __init__(self, rules: NamingRuleRepository):
        self.rules = rules
        self.fallback = TransactionNamer()

    def enrich(self, transactions):
        active_rules = self.rules.list_active()
        for transaction in transactions:
            if transaction.bank == "Capitec" and transaction.category == "Bank Fees":
                # Actual fees are separate posted movements, never rename into
                # a merchant purchase just because their description has one.
                transaction.display_name = "Capitec Bank Fee" if transaction.amount < 0 else "Capitec Fee Refund"
                transaction.naming_confidence = "high"
                transaction.name_source = "capitec_posted_fee"
                transaction.naming_reason = "Fee value came from Capitec's Fee column."
                continue
            # Let a user rule match either the untouched bank description OR the
            # current friendly name. This is useful when WalletWise has already
            # cleaned something like "FNB App Payment To Transport Leshens
            # Transport" into "Transport Leshens Transport" and the user creates
            # a rule from the friendly text they can see in the UI.
            searchable = " ".join(
                part
                for part in (transaction.raw_description, transaction.display_name)
                if part
            )
            matched = next(
                (
                    rule
                    for rule in active_rules
                    if NamingRuleRepository.matches(rule, searchable)
                ),
                None,
            )
            if matched and transaction.category == "Bank Fees" and transaction.bank == "Capitec":
                matched = None  # Preserve actual posted bank fee accounting.
            if matched:
                transaction.display_name = matched["displayName"]
                transaction.naming_confidence = "high"
                transaction.name_source = "database_rule"
                transaction.naming_reason = (
                    f"Matched naming rule: {matched['matchText']}"
                )

                # A naming rule is allowed to only name something. If its
                # category is Uncategorised, do NOT wipe a category that another
                # rule/heuristic can provide.
                rule_category = matched.get("category") or "Uncategorised"
                if rule_category != "Uncategorised":
                    transaction.category = rule_category
                    transaction.category_confidence = "high"
                    transaction.category_reason = (
                        f"Category came from naming rule: {matched['matchText']}"
                    )
            else:
                self.fallback.enrich_transaction(transaction)
        return transactions


class DatabaseAwareCategoryService:
    """Explicit category rules first, then medium-confidence heuristic hints."""

    def __init__(self, rules: CategoryRuleRepository):
        self.rules = rules
        self.fallback = CategoryService()

    def classify(self, transactions):
        active_rules = self.rules.list_active()
        for transaction in transactions:
            if transaction.bank == "Capitec" and transaction.category == "Bank Fees":
                continue  # Explicit posted bank fees must never be reclassified by a user rule.
            searchable = " ".join(
                part
                for part in (transaction.raw_description, transaction.display_name)
                if part
            )
            matched = next(
                (
                    rule
                    for rule in active_rules
                    if CategoryRuleRepository.matches(rule, searchable)
                ),
                None,
            )
            if matched:
                transaction.category = matched["category"]
                transaction.category_confidence = matched["confidence"]
                transaction.category_reason = (
                    f"Matched category rule: {matched['matchText']}"
                )
            else:
                self.fallback.classify_transaction(transaction)
        return transactions


class StatementProcessingService:
    def __init__(
        self,
        *,
        dataset_repository: DatasetRepository,
        rule_repository: NamingRuleRepository,
        category_rule_repository: CategoryRuleRepository,
        salary_source_repository: SalarySourceRepository,
        data_directory: str | Path,
    ):
        self.dataset_repository = dataset_repository
        self.rule_repository = rule_repository
        self.category_rule_repository = category_rule_repository
        self.salary_source_repository = salary_source_repository
        self.data_directory = Path(data_directory)
        self.upload_directory = self.data_directory / "uploads"
        self.upload_directory.mkdir(parents=True, exist_ok=True)
        self.detector = BankDetector()
        self.validator = TransactionValidator()
        self.confidence = ConfidenceService()
        self.namer = DatabaseAwareTransactionNamer(rule_repository)
        self.role_classifier = FinancialRoleService()
        self.categorizer = DatabaseAwareCategoryService(category_rule_repository)

    def process_pdf_bytes(self, *, filename: str, contents: bytes) -> dict:
        if not contents.startswith(b"%PDF"):
            raise ValueError("The uploaded file does not appear to be a valid PDF.")

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".pdf",
                dir=self.upload_directory,
                delete=False,
            ) as handle:
                handle.write(contents)
                temp_path = Path(handle.name)

            bank = self.detector.detect(str(temp_path))
            tesseract_cmd = os.getenv(
                "TESSERACT_CMD",
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            )
            config = AppConfig(
                pdf_file=str(temp_path),
                output_file=str(self.data_directory / "WalletWise_Statement.xlsx"),
                tesseract_cmd=tesseract_cmd,
                generate_debug_pdfs=os.getenv("WALLETWISE_IMPORT_DEBUG", "0").lower() in {"1", "true", "yes"},
                debug_prefix=str(self.data_directory / "debug" / f"import-{uuid.uuid4().hex[:8]}"),
            )

            if config.generate_debug_pdfs:
                (self.data_directory / "debug").mkdir(parents=True, exist_ok=True)
            extractor = ExtractorRegistry.create(bank, config)
            result = extractor.extract(str(temp_path))

            self.namer.enrich(result.transactions)
            self.role_classifier.classify(result.transactions)
            self.categorizer.classify(result.transactions)
            self.validator.validate(result.transactions)
            self.confidence.evaluate(result.transactions)

            statement_id = f"stmt-{uuid.uuid4().hex[:12]}"
            transaction_pairs = [
                (f"tx-{uuid.uuid4().hex[:12]}", transaction)
                for transaction in result.transactions
            ]

            statement = self.dataset_repository.append_statement(
                statement_id=statement_id,
                filename=Path(filename).name,
                bank=result.bank,
                file_hash=hashlib.sha256(contents).hexdigest(),
                transactions=transaction_pairs,
                statement_period=result.statement_period,
            )

            # A salary source only becomes trusted after the user confirms it.
            # Once trusted, apply it to newly imported and overlapping history so
            # the user does not need to mark the same employer every month.
            saved_salary_sources = self.salary_source_repository.list_active()
            if saved_salary_sources:
                for item in self.dataset_repository.list_transactions():
                    if item.get("isSalary") or item.get("salaryExcluded"):
                        continue
                    if any(
                        SalaryDetectionService.matches_source(item, source["matchText"])
                        for source in saved_salary_sources
                    ):
                        self.dataset_repository.set_salary_marker(item["id"], True)

            stored_transactions = self.dataset_repository.list_transactions()
            return {
                **statement,
                "status": "processed",
                "totalTransactionCount": len(stored_transactions),
                "transactions": stored_transactions,
                "statements": self.dataset_repository.list_statements(),
                "coverage": self.dataset_repository.coverage(),
                "unassignedCount": len(result.unassigned_rows),
                "periodDetection": result.statement_period or {"verified": False},
                "periodDebugFiles": result.debug_files if config.generate_debug_pdfs else [],
            }
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

from __future__ import annotations

import os

from dotenv import load_dotenv
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from walletwise.exceptions import UnsupportedBankError
from walletwise.extraction.registry import ExtractorRegistry
from walletwise.processing import FinancialRoleService

from .database import Database
from .ai_chat_repository import AIChatRepository
from .ai_service import AIConfigurationError, AIProviderError, GroqAIService
from .wally_engine import WallyAnswerEngine
from .wally_trace import footer, header, line, new_trace_id, path, section
from .repositories import (
    CategoryRepository,
    CategoryRuleRepository,
    DatasetRepository,
    NamingRuleRepository,
    SalarySourceRepository,
    SettingsRepository,
)
from .review_grouping import ReviewGroupingService
from .review_service import ReviewService
from .salary_detection import SalaryDetectionService
from .reporting import ReportingService
from .schemas import (
    BatchTransactionUpdate,
    BudgetProfilePayload,
    CategoryPayload,
    CategoryRulePayload,
    NamingRulePayload,
    ReviewConfirmationPayload,
    ReportExplorerPayload,
    SalaryMarkerPayload,
    SalarySourceConfirmationPayload,
    TrackingSettingsPayload,
    TransactionUpdate,
    AIAskPayload,
    AIChatRenamePayload,
)
from .services import StatementProcessingService


BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")
DATA_DIR = Path(os.getenv("WALLETWISE_DATA_DIR", BACKEND_DIR / "data"))
DB_PATH = Path(os.getenv("WALLETWISE_DB_PATH", DATA_DIR / "walletwise.db"))
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


database = Database(DB_PATH)
dataset_repository = DatasetRepository(database)
rule_repository = NamingRuleRepository(database)
category_repository = CategoryRepository(database)
category_rule_repository = CategoryRuleRepository(database)
settings_repository = SettingsRepository(database)
salary_source_repository = SalarySourceRepository(database)
ai_chat_repository = AIChatRepository(database)
review_service = ReviewService(dataset_repository)

statement_service = StatementProcessingService(
    dataset_repository=dataset_repository,
    rule_repository=rule_repository,
    category_rule_repository=category_rule_repository,
    salary_source_repository=salary_source_repository,
    data_directory=DATA_DIR,
)

# Re-apply saved/default rules to an existing test dataset when the backend
# starts. This is deliberately limited to uncertain rows, so user-confirmed
# high-confidence choices are preserved.
dataset_repository.apply_naming_rules_to_uncertain(rule_repository.list_active())
dataset_repository.apply_category_rules_to_uncertain(
    category_rule_repository.list_active()
)
dataset_repository.backfill_financial_roles(FinancialRoleService())
dataset_repository.backfill_fingerprints()
dataset_repository.backfill_statement_metadata()
for _transaction in dataset_repository.list_transactions():
    if _transaction.get("isSalary") or _transaction.get("salaryExcluded"):
        continue
    if any(
        SalaryDetectionService.matches_source(_transaction, _source["matchText"])
        for _source in salary_source_repository.list_active()
    ):
        dataset_repository.set_salary_marker(_transaction["id"], True)

app = FastAPI(
    title="WalletWise MVP API",
    version="0.9.0",
    description="Python backend for WalletWise statement extraction and MVP testing.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def imported_banks() -> list[str]:
    """Only show banks with statements actually imported, not all extractor plugins."""
    return sorted({item['bank'] for item in dataset_repository.list_statements() if item.get('bank')}, key=str.casefold)


def resolve_bank(bank: str | None) -> str | None:
    """Normalize an all/single/multiple bank selection for every financial endpoint."""
    value = (bank or 'all').strip()
    if not value or value.casefold() == 'all':
        return None
    parts = [part.strip() for part in value.split(',')]
    if not parts or any(not part or part.casefold() == 'all' for part in parts):
        raise HTTPException(status_code=400, detail='Choose valid bank names or All banks.')
    allowed = {name.casefold(): name for name in (*ExtractorRegistry.SUPPORTED_BANKS, *imported_banks())}
    selected = set()
    for part in parts:
        canonical = allowed.get(part.casefold())
        if canonical is None:
            raise HTTPException(status_code=400, detail=f'Unsupported bank filter: {part}')
        selected.add(canonical)
    return ','.join(sorted(selected, key=str.casefold))


def bank_transactions(bank: str | None) -> list[dict]:
    return dataset_repository.list_transactions(bank=resolve_bank(bank))


def bank_coverage(bank: str | None) -> dict:
    return dataset_repository.coverage(bank=resolve_bank(bank))


@app.get("/api/assistant/status")
def assistant_status():
    return GroqAIService.status()


@app.get("/api/assistant/chats")
def assistant_chats():
    return ai_chat_repository.list_chats()


@app.get("/api/assistant/chats/{chat_id}")
def assistant_chat(chat_id: str):
    try:
        return ai_chat_repository.get(chat_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat not found.")


@app.patch("/api/assistant/chats/{chat_id}")
def rename_assistant_chat(chat_id: str, payload: AIChatRenamePayload):
    try:
        return ai_chat_repository.rename(chat_id, payload.title)
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/assistant/chats/{chat_id}")
def delete_assistant_chat(chat_id: str):
    try:
        ai_chat_repository.delete(chat_id)
        return {"deleted": True, "id": chat_id}
    except KeyError:
        raise HTTPException(status_code=404, detail="Chat not found.")


@app.post("/api/assistant/ask")
def assistant_ask(payload: AIAskPayload):
    trace_id = new_trace_id()
    header(trace_id, "WALLY SEMANTIC ROUTING TRACE")
    section(trace_id, "INPUT - FRONTEND -> BACKEND", {
        "endpoint": "POST /api/assistant/ask",
        "jsonBody": payload.model_dump(mode="json"),
        "apiKeyFromBrowser": False,
    })

    try:
        # Persisted chat history is UI history, not automatic provider history.
        # The backend builds a tiny AI-facing memory from the saved chat.
        if payload.conversationId:
            chat = ai_chat_repository.get_summary(payload.conversationId)
            conversation_summary, history = ai_chat_repository.get_ai_memory(chat["id"])
            # Do not let prior ALL/FNB/Capitec financial details leak into a
            # different bank's reasoning. Single-bank queries are standalone.
            if resolve_bank(payload.bank) is not None:
                conversation_summary, history = {}, []
        else:
            chat = None
            conversation_summary, history = {}, []

        selected_bank = resolve_bank(payload.bank)
        transactions = bank_transactions(payload.bank)
        section(trace_id, "LOCAL INPUTS AVAILABLE BEFORE ROUTING", {
            "bankScope": selected_bank or "all",
            "conversationId": payload.conversationId,
            "chatFound": chat is not None,
            "compactSavedChatMemory": conversation_summary,
            "recentSavedMessagesAvailableLocally": len(history),
            "transactionsAvailableLocally": len(transactions),
            "coverage": bank_coverage(payload.bank),
            "trackingSettings": settings_repository.get_all(),
            "note": "None of the financial data is sent externally until the validated query plan explicitly chooses the AI-answer path.",
        })
        result = WallyAnswerEngine.answer(
            question=payload.question,
            transactions=transactions,
            settings=settings_repository.get_all(),
            coverage=bank_coverage(payload.bank),
            history=history,
            conversation_summary=conversation_summary,
            trace_id=trace_id,
        )
        answer = result.answer
        data_used = result.data_used
        data_used["traceId"] = trace_id
        data_used["bankScope"] = selected_bank or "all"
        model = result.model

        if chat is None:
            chat = ai_chat_repository.create(payload.question)

        ai_chat_repository.append_exchange(
            chat_id=chat["id"],
            question=payload.question,
            answer=answer,
            data_used=data_used,
            model=model,
        )
        chat = ai_chat_repository.get_summary(chat["id"])
        response_payload = {
            "answer": answer,
            "provider": result.provider,
            "model": model,
            "aiUsed": result.ai_used,
            "dataUsed": data_used,
            "conversationId": chat["id"],
            "conversationTitle": chat["title"],
            "conversationSummary": chat["summary"],
            "saved": True,
            "traceId": trace_id,
        }
        section(trace_id, "OUTPUT - BACKEND -> FRONTEND", {
            "chatSavedLocally": True,
            "jsonResponse": response_payload,
        })
        footer(trace_id, "WALLY ROUTING TRACE COMPLETE")
        return response_payload
    except KeyError:
        line(trace_id, "ERROR: saved chat not found.")
        footer(trace_id, "REQUEST FAILED")
        raise HTTPException(status_code=404, detail="Saved chat not found.")
    except ValueError as exc:
        line(trace_id, f"ERROR: {exc}")
        footer(trace_id, "REQUEST FAILED")
        raise HTTPException(status_code=400, detail=str(exc))
    except AIConfigurationError as exc:
        line(trace_id, f"AI CONFIGURATION ERROR: {exc}")
        footer(trace_id, "REQUEST FAILED")
        raise HTTPException(status_code=503, detail=str(exc))
    except AIProviderError as exc:
        line(trace_id, f"AI PROVIDER ERROR: {exc}")
        footer(trace_id, "REQUEST FAILED")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/health")
def health():
    return {"status": "ok", "supportedBanks": list(ExtractorRegistry.SUPPORTED_BANKS)}


@app.get("/api/dataset")
def dataset(bank: str | None = None):
    selected = resolve_bank(bank)
    statements = dataset_repository.list_statements()  # Upload management always shows every bank.
    selected_set = set(selected.split(',')) if selected else set()
    matching_statements = [item for item in statements if not selected or item['bank'] in selected_set]
    return {
        "statement": matching_statements[0] if matching_statements else None,
        "statements": statements,
        "availableBanks": imported_banks(),
        "selectedBank": selected or "all",
        "coverage": bank_coverage(bank),
        "settings": settings_repository.get_all(),
        "transactions": bank_transactions(bank),
    }


@app.get("/api/statements")
def statements():
    return dataset_repository.list_statements()


@app.delete("/api/statements/{statement_id}")
def delete_statement(statement_id: str):
    try:
        deleted = dataset_repository.delete_statement(statement_id)
        return {
            **deleted,
            "statements": dataset_repository.list_statements(),
            "coverage": dataset_repository.coverage(),
            "transactions": dataset_repository.list_transactions(),
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="Statement not found.")


@app.get("/api/coverage")
def coverage(bank: str | None = None):
    return bank_coverage(bank)


@app.get("/api/tracking-settings")
def tracking_settings():
    return settings_repository.get_all()


@app.put("/api/tracking-settings")
def update_tracking_settings(payload: TrackingSettingsPayload):
    try:
        settings = settings_repository.get_all()
        if "defaultTrackingView" in payload.model_fields_set:
            settings = settings_repository.update(default_tracking_view=payload.defaultTrackingView)
        if "expectedPayday" in payload.model_fields_set:
            if payload.expectedPayday is None:
                settings = settings_repository.clear_expected_payday()
            else:
                settings = settings_repository.update(expected_payday=payload.expectedPayday)
        return settings
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/transactions")
def transactions(bank: str | None = None):
    return bank_transactions(bank)


@app.get("/api/review-groups")
def review_groups(bank: str | None = None):
    return ReviewGroupingService.review_buckets(bank_transactions(bank))


@app.post("/api/review/confirm")
def confirm_review(payload: ReviewConfirmationPayload):
    try:
        return review_service.confirm(
            transaction_ids=payload.transactionIds,
            financial_role=payload.financialRole,
            category=payload.category,
            display_name=payload.displayName,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="One or more transactions were not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/budget-profile")
def budget_profile():
    return settings_repository.get_all().get("budgetProfile")


@app.put("/api/budget-profile")
def update_budget_profile(payload: BudgetProfilePayload):
    allowed = {
        "livingSituation": {"family", "rent", "own"},
        "householdResponsibility": {"self", "share", "support"},
        "transportStyle": {"car", "public", "ride", "remote"},
        "goal": {"emergency", "purchase", "debt", "breathing"},
        "budgetStyle": {"balanced", "conservative", "flexible"},
        "incomeStability": {"stable", "variable"},
        "emergencyFundLevel": {"none", "under_one", "one_to_three", "three_plus"},
    }
    allowed_estimates = {
        "housing", "groceries", "transport", "utilities", "mobileInternet",
        "insurance", "healthcare", "debtRepayments", "subscriptions",
        "eatingOut", "familySupport", "shoppingPersonal", "entertainment",
        "toiletries", "educationChildcare", "otherEssentials",
    }
    profile = payload.model_dump()
    for field, options in allowed.items():
        if profile[field] not in options:
            raise HTTPException(status_code=400, detail=f"Unsupported {field} value.")
    for key, value in (profile.get("expenseEstimates") or {}).items():
        if key not in allowed_estimates:
            raise HTTPException(status_code=400, detail=f"Unsupported budget estimate: {key}.")
        if value is not None and float(value) < 0:
            raise HTTPException(status_code=400, detail="Budget estimates cannot be negative.")
    return settings_repository.update_budget_profile(profile)


@app.get("/api/reports")
def reports(bank: str | None = None):
    return ReportingService.build(
        bank_transactions(bank),
        settings_repository.get_all(),
        bank_coverage(bank),
    )


@app.post("/api/reports/explore")
def explore_report(payload: ReportExplorerPayload, bank: str | None = None):
    try:
        start = date.fromisoformat(payload.startDate) if payload.startDate else None
        end = date.fromisoformat(payload.endDate) if payload.endDate else None
        if start and end and end < start:
            raise ValueError("End date must be on or after the start date.")
        return ReportingService.build_explorer(
            bank_transactions(bank),
            start=start,
            end=end,
            category=payload.category,
            merchant=payload.merchant,
            financial_role=payload.financialRole,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/reports/custom")
def custom_report(start: date, end: date, bank: str | None = None):
    try:
        return ReportingService.build_custom_period(
            bank_transactions(bank), start, end
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/transactions/batch")
def update_transactions_batch(payload: BatchTransactionUpdate):
    updates = payload.model_dump(exclude_none=True)
    transaction_ids = updates.pop("transactionIds", [])
    try:
        return dataset_repository.update_transactions(transaction_ids, updates)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="One or more transactions were not found.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/transactions/{transaction_id}")
def update_transaction(transaction_id: str, payload: TransactionUpdate):
    try:
        return dataset_repository.update_transaction(
            transaction_id,
            payload.model_dump(exclude_none=True),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/transactions/{transaction_id}/salary")
def set_salary_marker(transaction_id: str, payload: SalaryMarkerPayload):
    try:
        return dataset_repository.set_salary_marker(transaction_id, payload.isSalary)
    except KeyError:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/income-overview")
def income_overview(bank: str | None = None):
    return SalaryDetectionService.build_income_overview(
        bank_transactions(bank),
        salary_source_repository.list_active(),
    )


@app.post("/api/income/salary-source")
def confirm_salary_source(payload: SalarySourceConfirmationPayload):
    try:
        transaction = dataset_repository.get_transaction(payload.transactionId)
    except KeyError:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    if transaction.get("financialRole") != "income" or float(transaction.get("amount") or 0) <= 0:
        raise HTTPException(status_code=400, detail="Only incoming income can be confirmed as a salary source.")

    match_text = SalaryDetectionService.source_key(transaction)
    matching = [
        item for item in dataset_repository.list_transactions()
        if SalaryDetectionService.matches_source(item, match_text)
    ]
    matching_ids = {item["id"] for item in matching}

    selected_ids = (
        set(payload.selectedTransactionIds)
        if payload.selectedTransactionIds is not None
        else set(matching_ids)
    )
    if not selected_ids:
        raise HTTPException(status_code=400, detail="Select at least one payment to use as a salary anchor.")
    if not selected_ids.issubset(matching_ids):
        raise HTTPException(status_code=400, detail="One or more selected payments do not belong to this salary source.")

    source = salary_source_repository.create_or_enable(
        match_text,
        transaction.get("displayName") or transaction.get("rawDescription") or "Salary",
    )

    updated_ids = []
    excluded_ids = []
    for item in matching:
        if item["id"] in selected_ids:
            dataset_repository.set_salary_marker(item["id"], True)
            updated_ids.append(item["id"])
        else:
            dataset_repository.update_transaction(
                item["id"],
                {"isSalary": False, "salaryExcluded": True},
            )
            excluded_ids.append(item["id"])

    return {
        "source": source,
        "updatedTransactionIds": updated_ids,
        "excludedTransactionIds": excluded_ids,
        "updatedCount": len(updated_ids),
        "overview": SalaryDetectionService.build_income_overview(
            dataset_repository.list_transactions(),
            salary_source_repository.list_active(),
        ),
    }


@app.delete("/api/income/salary-source/{source_id}")
def remove_salary_source(source_id: int):
    """Forget a learned salary source and explicitly exclude its current rows.

    The income remains in WalletWise; only its salary/pay-cycle status is removed.
    Because excluded rows stay visible under Other income, the user can manually
    add the source back later.
    """
    try:
        source = salary_source_repository.get(source_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Salary source not found.")

    matching = [
        item for item in dataset_repository.list_transactions()
        if SalaryDetectionService.matches_source(item, source["matchText"])
    ]

    salary_source_repository.deactivate(source_id)
    updated_ids = []
    for item in matching:
        dataset_repository.set_salary_marker(item["id"], False)
        updated_ids.append(item["id"])

    return {
        "removed": True,
        "sourceId": source_id,
        "updatedTransactionIds": updated_ids,
        "overview": SalaryDetectionService.build_income_overview(
            dataset_repository.list_transactions(),
            salary_source_repository.list_active(),
        ),
    }


@app.get("/api/categories")
def categories():
    return [item['name'] for item in category_repository.overview()]


@app.get('/api/categories/overview')
def categories_overview(bank: str | None = None):
    return category_repository.overview(bank=resolve_bank(bank))


@app.patch('/api/categories/{category_name}')
def rename_category(category_name: str, payload: CategoryPayload):
    try:
        return category_repository.rename(category_name, payload.name)
    except KeyError:
        raise HTTPException(status_code=404, detail='Category not found.')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class CategoryMergePayload(BaseModel):
    source: str
    target: str


@app.post('/api/categories/merge')
def merge_categories(payload: CategoryMergePayload):
    try:
        return category_repository.merge(payload.source, payload.target)
    except KeyError:
        raise HTTPException(status_code=404, detail='Category not found.')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete('/api/categories/{category_name}')
def delete_category(category_name: str):
    try:
        return category_repository.delete(category_name)
    except KeyError:
        raise HTTPException(status_code=404, detail='Category not found.')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/categories", status_code=201)
def create_category(payload: CategoryPayload):
    try:
        return category_repository.create(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/statements/process")
async def process_statement(file: UploadFile = File(...)):
    filename = file.filename or "statement.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a PDF bank statement.",
        )

    contents = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="PDF is too large. Maximum size is 20 MB.",
        )

    try:
        return await run_in_threadpool(
            statement_service.process_pdf_bytes,
            filename=filename,
            contents=contents,
        )
    except UnsupportedBankError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"WalletWise could not process this statement: {exc}",
        ) from exc


@app.get("/api/naming-rules")
def naming_rules():
    return rule_repository.list_all()


@app.post("/api/naming-rules", status_code=201)
def create_naming_rule(payload: NamingRulePayload):
    try:
        rule = rule_repository.create(payload.model_dump())
        affected = dataset_repository.apply_rule_to_existing(rule)
        recategorised = dataset_repository.apply_category_rules_to_uncertain(
            category_rule_repository.list_active()
        )
        return {
            **rule,
            "affectedTransactions": affected,
            "recategorisedTransactions": recategorised,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/naming-rules/{rule_id}")
def update_naming_rule(rule_id: int, payload: NamingRulePayload):
    try:
        rule = rule_repository.update(rule_id, payload.model_dump())
        affected = dataset_repository.apply_rule_to_existing(rule)
        recategorised = dataset_repository.apply_category_rules_to_uncertain(
            category_rule_repository.list_active()
        )
        return {
            **rule,
            "affectedTransactions": affected,
            "recategorisedTransactions": recategorised,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="Naming rule not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/naming-rules/{rule_id}", status_code=204)
def delete_naming_rule(rule_id: int):
    try:
        rule_repository.delete(rule_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Naming rule not found.")
    return None


@app.get("/api/category-rules")
def category_rules():
    return category_rule_repository.list_all()


@app.post("/api/category-rules", status_code=201)
def create_category_rule(payload: CategoryRulePayload):
    try:
        rule = category_rule_repository.create(payload.model_dump())
        affected = dataset_repository.apply_category_rule_to_existing(rule)
        return {**rule, "affectedTransactions": affected}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/category-rules/{rule_id}")
def update_category_rule(rule_id: int, payload: CategoryRulePayload):
    try:
        rule = category_rule_repository.update(rule_id, payload.model_dump())
        affected = dataset_repository.apply_category_rule_to_existing(rule)
        return {**rule, "affectedTransactions": affected}
    except KeyError:
        raise HTTPException(status_code=404, detail="Category rule not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/category-rules/{rule_id}", status_code=204)
def delete_category_rule(rule_id: int):
    try:
        category_rule_repository.delete(rule_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Category rule not found.")
    return None

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from walletwise.models import Transaction

from .database import Database


class RuleMatcher:
    ALLOWED_MATCH_TYPES = {"contains", "exact", "starts_with", "regex"}

    @classmethod
    def matches(cls, rule: dict, description: str) -> bool:
        text = description or ""
        needle = rule["matchText"]
        match_type = rule["matchType"]

        if match_type == "exact":
            return text.casefold() == needle.casefold()
        if match_type == "starts_with":
            return text.casefold().startswith(needle.casefold())
        if match_type == "regex":
            try:
                return re.search(needle, text, flags=re.IGNORECASE) is not None
            except re.error:
                return False
        return needle.casefold() in text.casefold()

    @classmethod
    def validate_match(cls, payload: dict) -> None:
        if not (payload.get("matchText") or "").strip():
            raise ValueError("Match text is required.")

        match_type = payload.get("matchType") or "contains"
        if match_type not in cls.ALLOWED_MATCH_TYPES:
            raise ValueError(f"Unsupported match type: {match_type}")

        if match_type == "regex":
            try:
                re.compile(payload["matchText"], flags=re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"Invalid regular expression: {exc}") from exc


class NamingRuleRepository(RuleMatcher):
    def __init__(self, database: Database):
        self.database = database

    def list_active(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, match_text, display_name, category, match_type, scope, active
                FROM naming_rules
                WHERE active = 1
                ORDER BY LENGTH(match_text) DESC, id ASC
                """
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_all(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, match_text, display_name, category, match_type, scope, active
                FROM naming_rules
                ORDER BY display_name COLLATE NOCASE ASC, id ASC
                """
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def create(self, payload: dict) -> dict:
        self._validate(payload)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO naming_rules (
                    match_text, display_name, category, match_type, scope, active
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["matchText"].strip(),
                    payload["displayName"].strip(),
                    payload.get("category") or "Uncategorised",
                    payload.get("matchType") or "contains",
                    payload.get("scope") or "global",
                    1 if payload.get("active", True) else 0,
                ),
            )
            rule_id = cursor.lastrowid
        return self.get(rule_id)

    def get(self, rule_id: int) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id, match_text, display_name, category, match_type, scope, active
                FROM naming_rules WHERE id = ?
                """,
                (rule_id,),
            ).fetchone()
        if row is None:
            raise KeyError(rule_id)
        return self._row_to_dict(row)

    def update(self, rule_id: int, payload: dict) -> dict:
        self._validate(payload)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE naming_rules
                SET match_text = ?, display_name = ?, category = ?, match_type = ?,
                    scope = ?, active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    payload["matchText"].strip(),
                    payload["displayName"].strip(),
                    payload.get("category") or "Uncategorised",
                    payload.get("matchType") or "contains",
                    payload.get("scope") or "global",
                    1 if payload.get("active", True) else 0,
                    rule_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(rule_id)
        return self.get(rule_id)

    def delete(self, rule_id: int) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM naming_rules WHERE id = ?", (rule_id,)
            )
            if cursor.rowcount == 0:
                raise KeyError(rule_id)

    @classmethod
    def _validate(cls, payload: dict) -> None:
        cls.validate_match(payload)
        if not (payload.get("displayName") or "").strip():
            raise ValueError("Friendly name is required.")

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row["id"],
            "matchText": row["match_text"],
            "displayName": row["display_name"],
            "category": row["category"],
            "matchType": row["match_type"],
            "scope": row["scope"],
            "active": bool(row["active"]),
        }


class CategoryRepository:
    def __init__(self, database: Database):
        self.database = database

    def list_active(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name FROM categories
                WHERE active = 1
                ORDER BY CASE WHEN name = 'Uncategorised' THEN 1 ELSE 0 END,
                         name COLLATE NOCASE
                """
            ).fetchall()
        return [{"id": row["id"], "name": row["name"]} for row in rows]

    def create(self, name: str) -> dict:
        name = " ".join((name or "").split()).strip()
        if not name:
            raise ValueError("Category name is required.")

        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT id, name FROM categories WHERE lower(name) = lower(?)",
                (name,),
            ).fetchone()
            if existing is not None:
                return {"id": existing["id"], "name": existing["name"]}

            cursor = connection.execute(
                "INSERT INTO categories (name, active) VALUES (?, 1)",
                (name,),
            )
            row = connection.execute(
                "SELECT id, name FROM categories WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return {"id": row["id"], "name": row["name"]}


class CategoryRuleRepository(RuleMatcher):
    def __init__(self, database: Database):
        self.database = database

    def list_active(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM category_rules
                WHERE active = 1
                ORDER BY priority DESC, LENGTH(match_text) DESC, id ASC
                """
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_all(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM category_rules ORDER BY priority DESC, id ASC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def create(self, payload: dict) -> dict:
        self._validate(payload)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO category_rules (
                    match_text, category, match_type, confidence, priority, active
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["matchText"].strip(),
                    payload["category"].strip(),
                    payload.get("matchType") or "contains",
                    payload.get("confidence") or "high",
                    int(payload.get("priority", 50)),
                    1 if payload.get("active", True) else 0,
                ),
            )
            rule_id = cursor.lastrowid
        return self.get(rule_id)

    def get(self, rule_id: int) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM category_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        if row is None:
            raise KeyError(rule_id)
        return self._row_to_dict(row)

    def update(self, rule_id: int, payload: dict) -> dict:
        self._validate(payload)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE category_rules
                SET match_text = ?, category = ?, match_type = ?, confidence = ?,
                    priority = ?, active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    payload["matchText"].strip(),
                    payload["category"].strip(),
                    payload.get("matchType") or "contains",
                    payload.get("confidence") or "high",
                    int(payload.get("priority", 50)),
                    1 if payload.get("active", True) else 0,
                    rule_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(rule_id)
        return self.get(rule_id)

    def delete(self, rule_id: int) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM category_rules WHERE id = ?", (rule_id,)
            )
            if cursor.rowcount == 0:
                raise KeyError(rule_id)

    @classmethod
    def _validate(cls, payload: dict) -> None:
        cls.validate_match(payload)
        if not (payload.get("category") or "").strip():
            raise ValueError("Category is required.")
        if (payload.get("confidence") or "high") not in {"high", "medium", "low"}:
            raise ValueError("Category confidence must be high, medium, or low.")

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row["id"],
            "matchText": row["match_text"],
            "category": row["category"],
            "matchType": row["match_type"],
            "confidence": row["confidence"],
            "priority": row["priority"],
            "active": bool(row["active"]),
        }



class SettingsRepository:
    VALID_TRACKING_VIEWS = {"calendar", "pay_cycle"}

    def __init__(self, database: Database):
        self.database = database

    def get_all(self) -> dict:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT key, value FROM app_settings").fetchall()
        values = {row["key"]: row["value"] for row in rows}
        payday = values.get("expected_payday") or ""
        budget_profile = None
        raw_budget_profile = values.get("budget_profile_json") or ""
        if raw_budget_profile:
            try:
                decoded = json.loads(raw_budget_profile)
                if isinstance(decoded, dict):
                    budget_profile = decoded
            except json.JSONDecodeError:
                budget_profile = None
        return {
            "defaultTrackingView": values.get("default_tracking_view") or "calendar",
            "expectedPayday": int(payday) if payday.isdigit() else None,
            "budgetProfile": budget_profile,
        }

    def update(self, *, default_tracking_view: str | None = None, expected_payday: int | None = None) -> dict:
        with self.database.connect() as connection:
            if default_tracking_view is not None:
                if default_tracking_view not in self.VALID_TRACKING_VIEWS:
                    raise ValueError("Tracking view must be calendar or pay_cycle.")
                connection.execute(
                    """
                    INSERT INTO app_settings (key, value) VALUES ('default_tracking_view', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                    """,
                    (default_tracking_view,),
                )
            if expected_payday is not None:
                if not 1 <= int(expected_payday) <= 31:
                    raise ValueError("Expected payday must be between 1 and 31.")
                connection.execute(
                    """
                    INSERT INTO app_settings (key, value) VALUES ('expected_payday', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                    """,
                    (str(int(expected_payday)),),
                )
        return self.get_all()


    def update_budget_profile(self, profile: dict) -> dict:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value) VALUES ('budget_profile_json', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (json.dumps(profile, ensure_ascii=False),),
            )
        return self.get_all()

    def clear_expected_payday(self) -> dict:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value) VALUES ('expected_payday', '')
                ON CONFLICT(key) DO UPDATE SET value = '', updated_at = CURRENT_TIMESTAMP
                """
            )
        return self.get_all()


class SalarySourceRepository:
    def __init__(self, database: Database):
        self.database = database

    def list_active(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, match_text, label, active FROM salary_sources WHERE active = 1 ORDER BY label COLLATE NOCASE"
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, source_id: int) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, match_text, label, active FROM salary_sources WHERE id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise KeyError(source_id)
        return self._row(row)

    def create_or_enable(self, match_text: str, label: str) -> dict:
        match_text = (match_text or "").strip()
        label = " ".join((label or "Salary").split()).strip() or "Salary"
        if not match_text:
            raise ValueError("Salary source could not be identified from this transaction.")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO salary_sources (match_text, label, active)
                VALUES (?, ?, 1)
                ON CONFLICT(match_text) DO UPDATE SET
                    label = excluded.label, active = 1, updated_at = CURRENT_TIMESTAMP
                """,
                (match_text, label),
            )
            row = connection.execute(
                "SELECT id, match_text, label, active FROM salary_sources WHERE match_text = ?",
                (match_text,),
            ).fetchone()
        return self._row(row)

    def deactivate(self, source_id: int) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE salary_sources SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (source_id,),
            )
            if cursor.rowcount == 0:
                raise KeyError(source_id)

    @staticmethod
    def _row(row) -> dict:
        return {
            "id": row["id"],
            "matchText": row["match_text"],
            "label": row["label"],
            "active": bool(row["active"]),
        }


class DatasetRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def transaction_fingerprint(*, bank: str, transaction: Transaction | dict) -> str:
        if isinstance(transaction, dict):
            date_value = transaction.get("date") or ""
            raw_description = transaction.get("rawDescription") or ""
            amount = transaction.get("amount")
            balance = transaction.get("balance")
            transaction_type = transaction.get("transactionType") or ""
        else:
            date_value = transaction.date.date().isoformat() if transaction.date else ""
            raw_description = transaction.raw_description or ""
            amount = transaction.amount
            balance = transaction.balance
            transaction_type = transaction.transaction_type or ""

        description = " ".join(raw_description.casefold().split())
        raw = "|".join(
            [
                (bank or "").casefold(),
                str(date_value),
                description,
                f"{float(amount):.2f}" if amount is not None else "",
                f"{float(balance):.2f}" if balance is not None else "",
                str(transaction_type).casefold(),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def append_statement(
        self,
        *,
        statement_id: str,
        filename: str,
        bank: str,
        file_hash: str,
        transactions: Iterable[tuple[str, Transaction]],
    ) -> dict:
        """Append a statement and link duplicate rows to one canonical transaction.

        Exact duplicate PDFs are rejected by file hash. Overlapping statements are
        allowed; matching transactions are linked to both statements rather than
        duplicated in the canonical transaction history.
        """
        transactions = list(transactions)
        processed_at = datetime.now(timezone.utc).isoformat()
        dates = [
            transaction.date.date().isoformat()
            for _, transaction in transactions
            if transaction.date is not None
            and transaction.transaction_type != "closing_balance"
            and transaction.financial_role != "balance"
        ]
        period_start = min(dates) if dates else None
        period_end = max(dates) if dates else None

        imported_count = 0
        duplicate_count = 0

        with self.database.connect() as connection:
            if file_hash:
                duplicate_file = connection.execute(
                    "SELECT id, filename FROM statements WHERE file_hash = ? LIMIT 1",
                    (file_hash,),
                ).fetchone()
                if duplicate_file is not None:
                    raise ValueError(
                        f"This PDF has already been imported as {duplicate_file['filename']}."
                    )

            connection.execute(
                """
                INSERT INTO statements (
                    id, filename, bank, processed_at, transaction_count,
                    imported_count, duplicate_count, period_start, period_end, file_hash
                ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
                """,
                (
                    statement_id,
                    filename,
                    bank,
                    processed_at,
                    len(transactions),
                    period_start,
                    period_end,
                    file_hash,
                ),
            )

            for position, (proposed_id, transaction) in enumerate(transactions):
                fingerprint = self.transaction_fingerprint(bank=bank, transaction=transaction)
                existing = connection.execute(
                    "SELECT id FROM transactions WHERE fingerprint = ? LIMIT 1",
                    (fingerprint,),
                ).fetchone()

                if existing is not None:
                    transaction_id = existing["id"]
                    duplicate_count += 1
                else:
                    transaction_id = proposed_id
                    imported_count += 1
                    connection.execute(
                        """
                        INSERT INTO transactions (
                            id, statement_id, position, date, raw_description, display_name,
                            category, category_confidence, category_reason,
                            financial_role, role_confidence, role_reason, amount,
                            transaction_type, balance, bank_charges, bank,
                            extraction_confidence, naming_confidence, name_source, name_reason,
                            review_reasons, source_page, ocr_used, raw_date, raw_amount,
                            raw_balance, raw_charges, human_verified, fingerprint, is_salary
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            transaction_id,
                            statement_id,
                            position,
                            transaction.date.date().isoformat() if transaction.date else None,
                            transaction.raw_description,
                            transaction.display_name,
                            transaction.category,
                            transaction.category_confidence,
                            transaction.category_reason,
                            transaction.financial_role,
                            transaction.role_confidence,
                            transaction.role_reason,
                            transaction.amount,
                            transaction.transaction_type,
                            transaction.balance,
                            transaction.bank_charges,
                            transaction.bank,
                            transaction.confidence,
                            transaction.naming_confidence,
                            transaction.name_source,
                            transaction.naming_reason,
                            self.database.encode_list(transaction.review_reasons),
                            transaction.source_page,
                            1 if transaction.ocr_used else 0,
                            transaction.raw_date,
                            transaction.raw_amount,
                            transaction.raw_balance,
                            transaction.raw_charges,
                            0,
                            fingerprint,
                            0,
                        ),
                    )

                connection.execute(
                    """
                    INSERT OR IGNORE INTO statement_transactions (
                        statement_id, transaction_id, source_position
                    ) VALUES (?, ?, ?)
                    """,
                    (statement_id, transaction_id, position),
                )

            connection.execute(
                """
                UPDATE statements
                SET imported_count = ?, duplicate_count = ?
                WHERE id = ?
                """,
                (imported_count, duplicate_count, statement_id),
            )

        return self.get_statement(statement_id)

    # Kept as a compatibility alias for older code paths. It now appends.
    def replace_dataset(self, **kwargs) -> None:
        self.append_statement(file_hash="", **kwargs)

    def list_transactions(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT t.*, COUNT(st.statement_id) AS source_statement_count
                FROM transactions t
                LEFT JOIN statement_transactions st ON st.transaction_id = t.id
                GROUP BY t.id
                ORDER BY t.date ASC, t.position ASC, t.id ASC
                """
            ).fetchall()
        return [self._transaction_row(row) for row in rows]

    def list_statements(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*,
                       COUNT(st.transaction_id) AS linked_transaction_count
                FROM statements s
                LEFT JOIN statement_transactions st ON st.statement_id = s.id
                GROUP BY s.id
                ORDER BY COALESCE(s.period_start, s.processed_at) DESC, s.processed_at DESC
                """
            ).fetchall()
        return [self._statement_row(row) for row in rows]

    def get_statement(self, statement_id: str) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, COUNT(st.transaction_id) AS linked_transaction_count
                FROM statements s
                LEFT JOIN statement_transactions st ON st.statement_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (statement_id,),
            ).fetchone()
        if row is None:
            raise KeyError(statement_id)
        return self._statement_row(row)

    def current_statement(self) -> dict | None:
        statements = self.list_statements()
        return statements[0] if statements else None

    def delete_statement(self, statement_id: str) -> dict:
        """Delete one imported statement without deleting transactions shared by another."""
        with self.database.connect() as connection:
            statement = connection.execute(
                "SELECT id, filename FROM statements WHERE id = ?",
                (statement_id,),
            ).fetchone()
            if statement is None:
                raise KeyError(statement_id)

            transaction_rows = connection.execute(
                "SELECT transaction_id FROM statement_transactions WHERE statement_id = ?",
                (statement_id,),
            ).fetchall()
            transaction_ids = [row["transaction_id"] for row in transaction_rows]

            for transaction_id in transaction_ids:
                other = connection.execute(
                    """
                    SELECT statement_id FROM statement_transactions
                    WHERE transaction_id = ? AND statement_id != ?
                    ORDER BY statement_id LIMIT 1
                    """,
                    (transaction_id, statement_id),
                ).fetchone()
                owner = connection.execute(
                    "SELECT statement_id FROM transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if owner and owner["statement_id"] == statement_id and other is not None:
                    connection.execute(
                        "UPDATE transactions SET statement_id = ? WHERE id = ?",
                        (other["statement_id"], transaction_id),
                    )

            # Delete the source links first, then canonical rows with no remaining source.
            connection.execute(
                "DELETE FROM statement_transactions WHERE statement_id = ?",
                (statement_id,),
            )
            for transaction_id in transaction_ids:
                remaining = connection.execute(
                    "SELECT 1 FROM statement_transactions WHERE transaction_id = ? LIMIT 1",
                    (transaction_id,),
                ).fetchone()
                if remaining is None:
                    connection.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

            connection.execute("DELETE FROM statements WHERE id = ?", (statement_id,))

        return {"statementId": statement_id, "filename": statement["filename"], "deleted": True}

    def coverage(self) -> dict:
        statements = self.list_statements()
        ranges = []
        for statement in statements:
            start = statement.get("periodStart")
            end = statement.get("periodEnd")
            if start and end:
                ranges.append((date.fromisoformat(start), date.fromisoformat(end), statement["statementId"]))

        if not ranges:
            return {"start": None, "end": None, "months": [], "missingMonths": []}

        overall_start = min(item[0] for item in ranges)
        overall_end = max(item[1] for item in ranges)
        months = []
        cursor = date(overall_start.year, overall_start.month, 1)
        end_month = date(overall_end.year, overall_end.month, 1)

        while cursor <= end_month:
            if cursor.month == 12:
                next_month = date(cursor.year + 1, 1, 1)
            else:
                next_month = date(cursor.year, cursor.month + 1, 1)
            month_end = next_month - timedelta(days=1)
            total_days = month_end.day
            covered_days: set[date] = set()
            source_ids: set[str] = set()
            for start, end, statement_id in ranges:
                overlap_start = max(start, cursor)
                overlap_end = min(end, month_end)
                if overlap_start <= overlap_end:
                    source_ids.add(statement_id)
                    day = overlap_start
                    while day <= overlap_end:
                        covered_days.add(day)
                        day += timedelta(days=1)

            ratio = len(covered_days) / total_days if total_days else 0
            if ratio >= 0.80:
                status = "covered"
            elif ratio > 0:
                status = "partial"
            else:
                status = "missing"
            months.append(
                {
                    "month": cursor.strftime("%Y-%m"),
                    "label": cursor.strftime("%B %Y"),
                    "status": status,
                    "coveredDays": len(covered_days),
                    "daysInMonth": total_days,
                    "coveragePct": round(ratio * 100, 1),
                    "statementIds": sorted(source_ids),
                }
            )
            cursor = next_month

        return {
            "start": overall_start.isoformat(),
            "end": overall_end.isoformat(),
            "months": months,
            "missingMonths": [item for item in months if item["status"] != "covered"],
            "note": "Coverage is estimated from the first and last dated transactions in each imported statement.",
        }

    def update_transaction(self, transaction_id: str, payload: dict) -> dict:
        payload = dict(payload or {})
        # A transaction that is no longer income cannot remain a salary anchor.
        if "financialRole" in payload and payload.get("financialRole") != "income":
            payload["isSalary"] = False
            payload["salaryExcluded"] = False

        allowed = {
            "displayName": "display_name",
            "category": "category",
            "categoryConfidence": "category_confidence",
            "categoryReason": "category_reason",
            "financialRole": "financial_role",
            "roleConfidence": "role_confidence",
            "roleReason": "role_reason",
            "namingConfidence": "naming_confidence",
            "nameReason": "name_reason",
            "reviewReasons": "review_reasons",
            "humanVerified": "human_verified",
            "isSalary": "is_salary",
            "salaryExcluded": "salary_excluded",
        }
        assignments, values = self._assignments(payload, allowed)
        if not assignments:
            return self.get_transaction(transaction_id)

        values.append(transaction_id)
        with self.database.connect() as connection:
            cursor = connection.execute(
                f"UPDATE transactions SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise KeyError(transaction_id)
        return self.get_transaction(transaction_id)

    def update_transactions(self, transaction_ids: list[str], payload: dict) -> list[dict]:
        unique_ids = list(dict.fromkeys(transaction_ids))
        if not unique_ids:
            return []

        allowed = {
            "displayName": "display_name",
            "category": "category",
            "categoryConfidence": "category_confidence",
            "categoryReason": "category_reason",
            "financialRole": "financial_role",
            "roleConfidence": "role_confidence",
            "roleReason": "role_reason",
            "namingConfidence": "naming_confidence",
            "nameReason": "name_reason",
            "humanVerified": "human_verified",
            "isSalary": "is_salary",
            "salaryExcluded": "salary_excluded",
        }
        assignments, values = self._assignments(payload, allowed)
        if not assignments:
            return [self.get_transaction(i) for i in unique_ids]

        placeholders = ",".join("?" for _ in unique_ids)
        with self.database.connect() as connection:
            existing = connection.execute(
                f"SELECT id FROM transactions WHERE id IN ({placeholders})",
                unique_ids,
            ).fetchall()
            existing_ids = {row["id"] for row in existing}
            missing = [i for i in unique_ids if i not in existing_ids]
            if missing:
                raise KeyError(missing[0])

            connection.execute(
                f"UPDATE transactions SET {', '.join(assignments)} WHERE id IN ({placeholders})",
                [*values, *unique_ids],
            )
        return [self.get_transaction(i) for i in unique_ids]

    def set_salary_marker(self, transaction_id: str, is_salary: bool) -> dict:
        transaction = self.get_transaction(transaction_id)
        if is_salary:
            if transaction.get("financialRole") != "income" or float(transaction.get("amount") or 0) <= 0:
                raise ValueError("Only incoming income transactions can be marked as salary.")
            return self.update_transaction(
                transaction_id,
                {
                    "isSalary": True,
                    "salaryExcluded": False,
                    "category": "Salary",
                    "categoryConfidence": "high",
                    "categoryReason": "Marked as primary salary by user",
                    "roleConfidence": "high",
                    "roleReason": "Salary is an incoming income transaction",
                },
            )
        updates = {"isSalary": False, "salaryExcluded": True}
        # If WalletWise only called this transaction "Salary" because the user
        # marked it as such, removing salary should not leave a misleading category.
        if str(transaction.get("category") or "").casefold() == "salary":
            updates.update({
                "category": "Uncategorised",
                "categoryConfidence": "unknown",
                "categoryReason": "Salary status removed by user; category needs review",
            })
        return self.update_transaction(transaction_id, updates)

    def get_transaction(self, transaction_id: str) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT t.*, COUNT(st.statement_id) AS source_statement_count
                FROM transactions t
                LEFT JOIN statement_transactions st ON st.transaction_id = t.id
                WHERE t.id = ?
                GROUP BY t.id
                """,
                (transaction_id,),
            ).fetchone()
        if row is None:
            raise KeyError(transaction_id)
        return self._transaction_row(row)

    def backfill_fingerprints(self) -> int:
        rows = self.list_transactions()
        updated = 0
        with self.database.connect() as connection:
            for row in rows:
                if row.get("fingerprint"):
                    continue
                fingerprint = self.transaction_fingerprint(bank=row.get("bank") or "", transaction=row)
                connection.execute(
                    "UPDATE transactions SET fingerprint = ? WHERE id = ?",
                    (fingerprint, row["id"]),
                )
                updated += 1
        return updated

    def backfill_statement_metadata(self) -> int:
        statements = self.list_statements()
        updated = 0
        with self.database.connect() as connection:
            for statement in statements:
                if statement.get("periodStart") and statement.get("periodEnd"):
                    continue
                row = connection.execute(
                    """
                    SELECT MIN(t.date) AS period_start, MAX(t.date) AS period_end
                    FROM statement_transactions st
                    JOIN transactions t ON t.id = st.transaction_id
                    WHERE st.statement_id = ?
                      AND t.date IS NOT NULL
                      AND t.transaction_type != 'closing_balance'
                      AND t.financial_role != 'balance'
                    """,
                    (statement["statementId"],),
                ).fetchone()
                if row and row["period_start"] and row["period_end"]:
                    connection.execute(
                        "UPDATE statements SET period_start = ?, period_end = ? WHERE id = ?",
                        (row["period_start"], row["period_end"], statement["statementId"]),
                    )
                    updated += 1
        return updated

    def backfill_financial_roles(self, role_service) -> int:
        rows = self.list_transactions()
        updated = 0
        for row in rows:
            if row.get("financialRole") not in {None, "", "unknown"}:
                continue
            date_value = datetime.fromisoformat(row["date"]) if row.get("date") else datetime.now()
            transaction = Transaction(
                date=date_value,
                raw_description=row.get("rawDescription") or "",
                display_name=row.get("displayName") or "",
                category=row.get("category") or "Uncategorised",
                category_confidence=row.get("categoryConfidence") or "unknown",
                category_reason=row.get("categoryReason") or "",
                amount=row.get("amount"),
                transaction_type=row.get("transactionType") or "unknown",
                balance=row.get("balance"),
                bank_charges=row.get("bankCharges"),
                bank=row.get("bank") or "",
            )
            role_service.classify_transaction(transaction)
            self.update_transaction(
                row["id"],
                {
                    "financialRole": transaction.financial_role,
                    "roleConfidence": transaction.role_confidence,
                    "roleReason": transaction.role_reason,
                    "category": transaction.category,
                    "categoryConfidence": transaction.category_confidence,
                    "categoryReason": transaction.category_reason,
                },
            )
            updated += 1
        return updated

    def apply_rule_to_existing(self, rule: dict) -> int:
        if not rule.get("active", True):
            return 0
        rows = self.list_transactions()
        matching_ids = [
            row["id"]
            for row in rows
            if NamingRuleRepository.matches(
                rule,
                " ".join(part for part in (row["rawDescription"], row["displayName"]) if part),
            )
        ]
        if not matching_ids:
            return 0
        rule_category = (rule.get("category") or "Uncategorised").strip()
        with self.database.connect() as connection:
            if rule_category != "Uncategorised":
                connection.executemany(
                    """
                    UPDATE transactions
                    SET display_name = ?, category = ?, naming_confidence = 'high',
                        category_confidence = 'high', name_source = 'database_rule',
                        name_reason = ?, category_reason = ?
                    WHERE id = ?
                    """,
                    [
                        (
                            rule["displayName"],
                            rule_category,
                            f"Matched naming rule: {rule['matchText']}",
                            f"Category came from naming rule: {rule['matchText']}",
                            transaction_id,
                        )
                        for transaction_id in matching_ids
                    ],
                )
            else:
                connection.executemany(
                    """
                    UPDATE transactions
                    SET display_name = ?, naming_confidence = 'high',
                        name_source = 'database_rule', name_reason = ?
                    WHERE id = ?
                    """,
                    [
                        (
                            rule["displayName"],
                            f"Matched naming rule: {rule['matchText']}",
                            transaction_id,
                        )
                        for transaction_id in matching_ids
                    ],
                )
        return len(matching_ids)

    def apply_naming_rules_to_uncertain(self, rules: list[dict]) -> int:
        rows = self.list_transactions()
        updates = []
        for row in rows:
            if row.get("namingConfidence") == "high":
                continue
            searchable = " ".join(
                part for part in (row.get("rawDescription"), row.get("displayName")) if part
            )
            matched = next(
                (rule for rule in rules if NamingRuleRepository.matches(rule, searchable)),
                None,
            )
            if matched is not None:
                updates.append((row["id"], matched))
        if not updates:
            return 0
        with self.database.connect() as connection:
            for transaction_id, rule in updates:
                rule_category = (rule.get("category") or "Uncategorised").strip()
                if rule_category != "Uncategorised":
                    connection.execute(
                        """
                        UPDATE transactions
                        SET display_name = ?, category = ?, naming_confidence = 'high',
                            category_confidence = 'high', name_source = 'database_rule',
                            name_reason = ?, category_reason = ?
                        WHERE id = ?
                        """,
                        (
                            rule["displayName"],
                            rule_category,
                            f"Matched naming rule: {rule['matchText']}",
                            f"Category came from naming rule: {rule['matchText']}",
                            transaction_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE transactions
                        SET display_name = ?, naming_confidence = 'high',
                            name_source = 'database_rule', name_reason = ?
                        WHERE id = ?
                        """,
                        (
                            rule["displayName"],
                            f"Matched naming rule: {rule['matchText']}",
                            transaction_id,
                        ),
                    )
        return len(updates)

    def apply_category_rules_to_uncertain(self, rules: list[dict]) -> int:
        rows = self.list_transactions()
        updates = []
        for row in rows:
            if (
                row.get("category")
                and row.get("category") != "Uncategorised"
                and row.get("categoryConfidence") == "high"
            ):
                continue
            searchable = " ".join(
                part for part in (row.get("rawDescription"), row.get("displayName")) if part
            )
            matched = next(
                (rule for rule in rules if CategoryRuleRepository.matches(rule, searchable)),
                None,
            )
            if matched is not None:
                updates.append((row["id"], matched))
        if not updates:
            return 0
        with self.database.connect() as connection:
            connection.executemany(
                """
                UPDATE transactions
                SET category = ?, category_confidence = ?, category_reason = ?
                WHERE id = ?
                """,
                [
                    (
                        rule["category"],
                        rule["confidence"],
                        f"Matched category rule: {rule['matchText']}",
                        transaction_id,
                    )
                    for transaction_id, rule in updates
                ],
            )
        return len(updates)

    def apply_category_rule_to_existing(self, rule: dict) -> int:
        if not rule.get("active", True):
            return 0
        rows = self.list_transactions()
        matching_ids = [
            row["id"]
            for row in rows
            if CategoryRuleRepository.matches(
                rule,
                " ".join([row["rawDescription"], row["displayName"]]),
            )
        ]
        if not matching_ids:
            return 0
        with self.database.connect() as connection:
            connection.executemany(
                """
                UPDATE transactions
                SET category = ?, category_confidence = ?, category_reason = ?
                WHERE id = ?
                """,
                [
                    (
                        rule["category"],
                        rule["confidence"],
                        f"Matched category rule: {rule['matchText']}",
                        transaction_id,
                    )
                    for transaction_id in matching_ids
                ],
            )
        return len(matching_ids)

    def _assignments(self, payload: dict, allowed: dict) -> tuple[list[str], list]:
        assignments = []
        values = []
        for key, column in allowed.items():
            if key not in payload:
                continue
            assignments.append(f"{column} = ?")
            value = payload[key]
            if key == "reviewReasons":
                value = self.database.encode_list(value)
            elif key in {"humanVerified", "isSalary", "salaryExcluded"}:
                value = 1 if value else 0
            values.append(value)
        return assignments, values

    @staticmethod
    def _statement_row(row) -> dict:
        return {
            "statementId": row["id"],
            "filename": row["filename"],
            "bank": row["bank"],
            "processedAt": row["processed_at"],
            "transactionCount": row["transaction_count"],
            "importedCount": row["imported_count"],
            "duplicateCount": row["duplicate_count"],
            "periodStart": row["period_start"],
            "periodEnd": row["period_end"],
            "linkedTransactionCount": row["linked_transaction_count"] if "linked_transaction_count" in row.keys() else row["transaction_count"],
        }

    @staticmethod
    def _transaction_row(row) -> dict:
        return {
            "id": row["id"],
            "date": row["date"],
            "displayName": row["display_name"],
            "rawDescription": row["raw_description"],
            "category": row["category"],
            "categoryConfidence": row["category_confidence"],
            "categoryReason": row["category_reason"],
            "financialRole": row["financial_role"],
            "roleConfidence": row["role_confidence"],
            "roleReason": row["role_reason"],
            "amount": row["amount"],
            "transactionType": row["transaction_type"],
            "balance": row["balance"],
            "bankCharges": row["bank_charges"],
            "bank": row["bank"],
            "extractionConfidence": row["extraction_confidence"],
            "namingConfidence": row["naming_confidence"],
            "nameSource": row["name_source"],
            "nameReason": row["name_reason"],
            "reviewReasons": Database.decode_list(row["review_reasons"]),
            "sourcePage": row["source_page"],
            "ocrUsed": bool(row["ocr_used"]),
            "rawDate": row["raw_date"],
            "rawAmount": row["raw_amount"],
            "rawBalance": row["raw_balance"],
            "rawCharges": row["raw_charges"],
            "humanVerified": bool(row["human_verified"]),
            "fingerprint": row["fingerprint"],
            "isSalary": bool(row["is_salary"]),
            "salaryExcluded": bool(row["salary_excluded"]) if "salary_excluded" in row.keys() else False,
            "canBeSalaryAnchor": (
                row["financial_role"] == "income"
                and float(row["amount"] or 0) > 0
                and row["transaction_type"] != "closing_balance"
            ),
            "sourceStatementCount": int(row["source_statement_count"]) if "source_statement_count" in row.keys() else 1,
        }

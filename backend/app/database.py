from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DEFAULT_CATEGORIES = [
    "Groceries",
    "Transport",
    "Eating Out",
    "Subscriptions",
    "Shopping",
    "Fuel",
    "Bills",
    "Bank Fees",
    "Salary",
    "Income",
    "Gift / Contribution",
    "Transfer",
    "Refund / Reimbursement",
    "Savings",
    "Entertainment",
    "Health",
    "Health & Personal Care",
    "Housing",
    "Insurance",
    "Healthcare",
    "Debt Repayments",
    "Toiletries",
    "Family Support",
    "Education",
    "Childcare",
    "Utilities",
    "Mobile & Internet",
    "Other",
    "Uncategorised",
]

DEFAULT_NAMING_RULES = [
    ("checkers", "Checkers", "Groceries"),
    ("shoprite", "Shoprite", "Groceries"),
    ("pick n pay", "Pick n Pay", "Groceries"),
    ("pnp", "Pick n Pay", "Groceries"),
    ("woolworths", "Woolworths", "Groceries"),
    ("spar", "SPAR", "Groceries"),
    ("takealot", "Takealot", "Shopping"),
    ("amazon", "Amazon", "Shopping"),
    ("netflix", "Netflix", "Subscriptions"),
    ("spotify", "Spotify", "Subscriptions"),
    ("uber eats", "Uber Eats", "Eating Out"),
    ("mr d", "Mr D", "Eating Out"),
    ("bolt", "Bolt", "Transport"),
    ("uber", "Uber", "Transport"),
    ("engen", "Engen", "Fuel"),
    ("shell", "Shell", "Fuel"),
    ("sasol", "Sasol", "Fuel"),
    ("clicks", "Clicks", "Health & Personal Care"),
    ("dis-chem", "Dis-Chem", "Health & Personal Care"),
    ("mcdonald", "McDonald's", "Eating Out"),
    ("kfc", "KFC", "Eating Out"),
    ("nando", "Nando's", "Eating Out"),
]

DEFAULT_CATEGORY_RULES = [
    (r"\buber\s*eats\b|\bubereats\b", "Eating Out", "regex", "high", 100),
    (r"\bmr\s*d\b|\bmrd\b", "Eating Out", "regex", "high", 100),
    (r"\bairtime\b", "Entertainment", "regex", "high", 90),
    (r"\bamazon\b", "Shopping", "regex", "high", 90),
    (r"\btakealot\b", "Shopping", "regex", "high", 90),
    (r"\bbolt\b", "Transport", "regex", "high", 80),
    (r"\buber\b", "Transport", "regex", "high", 80),
    (r"\btransport\b", "Transport", "regex", "medium", 60),
    (r"\bdata\b", "Entertainment", "regex", "high", 70),
    (r"\bfood\b", "Eating Out", "regex", "high", 50),
]


class Database:
    """SQLite schema and lightweight in-place migrations for WalletWise."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialise()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialise(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS statements (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    bank TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    transaction_count INTEGER NOT NULL DEFAULT 0,
                    imported_count INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    period_start TEXT,
                    period_end TEXT,
                    period_source TEXT NOT NULL DEFAULT '',
                    period_header TEXT NOT NULL DEFAULT '',
                    file_hash TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY,
                    statement_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    date TEXT,
                    raw_description TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'Uncategorised',
                    category_confidence TEXT NOT NULL DEFAULT 'unknown',
                    category_reason TEXT NOT NULL DEFAULT '',
                    financial_role TEXT NOT NULL DEFAULT 'unknown',
                    savings_direction TEXT NOT NULL DEFAULT '',
                    role_confidence TEXT NOT NULL DEFAULT 'unknown',
                    role_reason TEXT NOT NULL DEFAULT '',
                    amount REAL,
                    transaction_type TEXT NOT NULL DEFAULT 'unknown',
                    balance REAL,
                    bank_charges REAL,
                    bank TEXT NOT NULL DEFAULT '',
                    extraction_confidence TEXT NOT NULL DEFAULT 'high',
                    naming_confidence TEXT NOT NULL DEFAULT 'unknown',
                    name_source TEXT NOT NULL DEFAULT '',
                    name_reason TEXT NOT NULL DEFAULT '',
                    review_reasons TEXT NOT NULL DEFAULT '[]',
                    source_page INTEGER NOT NULL DEFAULT 0,
                    ocr_used INTEGER NOT NULL DEFAULT 0,
                    raw_date TEXT NOT NULL DEFAULT '',
                    raw_amount TEXT NOT NULL DEFAULT '',
                    raw_balance TEXT NOT NULL DEFAULT '',
                    raw_charges TEXT NOT NULL DEFAULT '',
                    human_verified INTEGER NOT NULL DEFAULT 0,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    is_salary INTEGER NOT NULL DEFAULT 0,
                    salary_excluded INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(statement_id) REFERENCES statements(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS statement_transactions (
                    statement_id TEXT NOT NULL,
                    transaction_id TEXT NOT NULL,
                    source_position INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(statement_id, transaction_id),
                    FOREIGN KEY(statement_id) REFERENCES statements(id) ON DELETE CASCADE,
                    FOREIGN KEY(transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS naming_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_text TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Uncategorised',
                    match_type TEXT NOT NULL DEFAULT 'contains',
                    scope TEXT NOT NULL DEFAULT 'global',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS category_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_text TEXT NOT NULL,
                    category TEXT NOT NULL,
                    match_type TEXT NOT NULL DEFAULT 'contains',
                    confidence TEXT NOT NULL DEFAULT 'high',
                    priority INTEGER NOT NULL DEFAULT 50,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS salary_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_text TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS ai_chats (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS ai_chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL DEFAULT '',
                    answer_json TEXT,
                    data_used_json TEXT,
                    model TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(chat_id) REFERENCES ai_chats(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_transactions_statement
                    ON transactions(statement_id, position);
                CREATE INDEX IF NOT EXISTS idx_statement_transactions_transaction
                    ON statement_transactions(transaction_id);
                CREATE INDEX IF NOT EXISTS idx_naming_rules_active
                    ON naming_rules(active);
                CREATE INDEX IF NOT EXISTS idx_category_rules_active
                    ON category_rules(active, priority DESC);
                CREATE INDEX IF NOT EXISTS idx_salary_sources_active
                    ON salary_sources(active);
                CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_chat
                    ON ai_chat_messages(chat_id, id);
                CREATE INDEX IF NOT EXISTS idx_ai_chats_updated
                    ON ai_chats(updated_at DESC);
                """
            )

            # Existing MVP databases are upgraded without wiping user data.
            for column, definition in (
                ("category_confidence", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("category_reason", "TEXT NOT NULL DEFAULT ''"),
                ("financial_role", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("savings_direction", "TEXT NOT NULL DEFAULT ''"),
                ("role_confidence", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("role_reason", "TEXT NOT NULL DEFAULT ''"),
                ("human_verified", "INTEGER NOT NULL DEFAULT 0"),
                ("fingerprint", "TEXT NOT NULL DEFAULT ''"),
                ("is_salary", "INTEGER NOT NULL DEFAULT 0"),
                ("salary_excluded", "INTEGER NOT NULL DEFAULT 0"),
            ):
                self._ensure_column(connection, "transactions", column, definition)

            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_transactions_fingerprint ON transactions(fingerprint)"
            )

            for column, definition in (
                ("imported_count", "INTEGER NOT NULL DEFAULT 0"),
                ("duplicate_count", "INTEGER NOT NULL DEFAULT 0"),
                ("period_start", "TEXT"),
                ("period_end", "TEXT"),
                ("period_source", "TEXT NOT NULL DEFAULT ''"),
                ("period_header", "TEXT NOT NULL DEFAULT ''"),
                ("file_hash", "TEXT NOT NULL DEFAULT ''"),
            ):
                self._ensure_column(connection, "statements", column, definition)

            # Saved Wally chats keep the complete local conversation for the UI,
            # plus a tiny summary/context used for future AI requests.
            for column, definition in (
                ("summary", "TEXT NOT NULL DEFAULT ''"),
                ("context_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                self._ensure_column(connection, "ai_chats", column, definition)

            # Older one-statement MVPs did not track imported/duplicate counts.
            # Treat their existing rows as originally imported rows.
            connection.execute(
                """
                UPDATE statements
                SET imported_count = transaction_count
                WHERE file_hash = '' AND imported_count = 0 AND transaction_count > 0
                """
            )

            # Backfill the source-link table for databases created by older MVPs.
            connection.execute(
                """
                INSERT OR IGNORE INTO statement_transactions (statement_id, transaction_id, source_position)
                SELECT statement_id, id, position FROM transactions
                WHERE statement_id IS NOT NULL AND statement_id != ''
                """
            )

            # Balance is metadata, not a user-facing category.
            connection.execute("UPDATE categories SET active = 0 WHERE lower(name) = 'balance'")

            self._seed_categories(connection)
            self._seed_naming_rules(connection)
            self._seed_category_rules(connection)
            self._seed_settings(connection)

    @staticmethod
    def _ensure_column(connection, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _seed_categories(connection: sqlite3.Connection) -> None:
        for name in DEFAULT_CATEGORIES:
            connection.execute(
                "INSERT OR IGNORE INTO categories (name, active) VALUES (?, 1)",
                (name,),
            )
        connection.execute("UPDATE categories SET active = 0 WHERE lower(name) = 'balance'")

    @staticmethod
    def _seed_naming_rules(connection: sqlite3.Connection) -> None:
        for match_text, display_name, category in DEFAULT_NAMING_RULES:
            existing = connection.execute(
                """
                SELECT id FROM naming_rules
                WHERE lower(match_text) = lower(?) AND match_type = 'contains'
                LIMIT 1
                """,
                (match_text,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO naming_rules (
                        match_text, display_name, category, match_type, scope, active
                    ) VALUES (?, ?, ?, 'contains', 'global', 1)
                    """,
                    (match_text, display_name, category),
                )

    @staticmethod
    def _seed_category_rules(connection: sqlite3.Connection) -> None:
        for match_text, category, match_type, confidence, priority in DEFAULT_CATEGORY_RULES:
            existing = connection.execute(
                """
                SELECT id FROM category_rules
                WHERE match_text = ? AND match_type = ?
                LIMIT 1
                """,
                (match_text, match_type),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO category_rules (
                        match_text, category, match_type, confidence, priority, active
                    ) VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (match_text, category, match_type, confidence, priority),
                )

    @staticmethod
    def _seed_settings(connection: sqlite3.Connection) -> None:
        defaults = {
            "default_tracking_view": "calendar",
            "expected_payday": "",
            "budget_profile_json": "",
        }
        for key, value in defaults.items():
            connection.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    @staticmethod
    def encode_list(values: list[str] | None) -> str:
        return json.dumps(values or [], ensure_ascii=False)

    @staticmethod
    def decode_list(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            return []

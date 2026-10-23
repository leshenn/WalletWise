from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Transaction:
    """Bank-independent WalletWise transaction."""

    date: datetime
    raw_description: str
    display_name: str = ""

    category: str = "Uncategorised"
    category_confidence: str = "unknown"  # high / medium / low / unknown
    category_reason: str = ""

    # What this movement means financially. This is intentionally separate from
    # transaction_type (debit / credit), because a credit can be income,
    # a refund, or a transfer from another account.
    financial_role: str = "unknown"  # expense / income / transfer / refund / balance / unknown
    role_confidence: str = "unknown"  # high / medium / low / unknown
    role_reason: str = ""

    # Standard WalletWise money convention:
    # debit / money out -> negative; credit / money in -> positive.
    amount: float | None = None
    transaction_type: str = "unknown"
    balance: float | None = None
    bank_charges: float | None = None
    bank: str = ""
    savings_direction: str = ""  # into / out / none; explicit Capitec classification

    # Extraction confidence is separate from interpretation confidence.
    confidence: str = "high"
    naming_confidence: str = "unknown"
    name_source: str = ""
    naming_reason: str = ""

    source_page: int = 0
    ocr_used: bool = False
    raw_date: str = ""
    raw_amount: str = ""
    raw_balance: str = ""
    raw_charges: str = ""
    review_reasons: list[str] = field(default_factory=list)

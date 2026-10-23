from dataclasses import dataclass, field

from .transaction import Transaction


@dataclass
class UnassignedRow:
    bank: str
    page: int
    raw_date: str = ""
    raw_description: str = ""
    raw_amount: str = ""
    raw_balance: str = ""
    reason: str = ""


@dataclass
class ExtractionResult:
    bank: str
    transactions: list[Transaction]
    page_counts: dict[int, int] = field(default_factory=dict)
    unassigned_rows: list[UnassignedRow] = field(default_factory=list)
    debug_files: list[str] = field(default_factory=list)
    statement_period: dict | None = None

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FNBRawRow:
    date: str = ""
    description: str = ""
    amount: str = ""
    balance: str = ""
    charges: str = ""
    page: int = 0
    y0: float = 0.0
    y1: float = 0.0
    ocr_used: bool = False


@dataclass
class FNBTransactionDraft:
    date: datetime
    raw_date: str
    description: str
    amount: str = ""
    balance: str = ""
    charges: str = ""
    page: int = 0
    y0: float = 0.0
    y1: float = 0.0
    ocr_used: bool = False
    record_type: str = "transaction"
    review_reasons: list[str] = field(default_factory=list)

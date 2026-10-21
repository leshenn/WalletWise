from dataclasses import dataclass


@dataclass(frozen=True)
class FNBConfig:
    """FNB-specific PDF/OCR settings. Nothing here leaks into the common schema."""

    statement_start_year: int | None = None
    date_order: str = "ascending"

    enable_ocr: bool = True
    tesseract_cmd: str | None = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    ocr_dpi: int = 450
    ocr_row_padding: float = 1.0
    ocr_min_confidence: float = 10.0
    ocr_side_trim: float = 1.3

    # Exact FNB transaction-table boundaries discovered from the current FNB layout.
    date_x0: float = 11.28
    date_x1: float = 36.72
    description_x0: float = 36.72
    description_x1: float = 420.48
    amount_x0: float = 420.48
    amount_x1: float = 480.72
    balance_x0: float = 480.72
    balance_x1: float = 544.80
    charges_x0: float = 544.80
    charges_x1: float = 581.52

    row_y_tolerance: float = 2.8
    split_row_max_gap: float = 5.5
    description_continuation_max_gap: float = 11.0

    @property
    def table_x0(self) -> float:
        return self.date_x0

    @property
    def table_x1(self) -> float:
        return self.charges_x1

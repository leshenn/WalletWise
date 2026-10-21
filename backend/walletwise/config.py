from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """Application-level settings that are independent of any bank layout."""

    pdf_file: str = "statement.pdf"
    output_file: str = "WalletWise_Statement.xlsx"

    # Bank-specific debug files use this prefix.
    debug_prefix: str = "WalletWise"
    generate_debug_pdfs: bool = True

    # Shared OCR executable location. Individual bank adapters choose how OCR is used.
    tesseract_cmd: str | None = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

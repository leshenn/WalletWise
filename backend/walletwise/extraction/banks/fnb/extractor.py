import pymupdf

from walletwise.config import AppConfig
from walletwise.extraction.base import BankStatementExtractor
from walletwise.models import ExtractionResult

from .config import FNBConfig
from .debug_pdf import FNBDebugPDFGenerator
from .normalizer import FNBNormalizer
from .ocr_service import FNBOCRService
from .pdf_extractor import FNBPDFExtractor
from .row_parser import FNBRowParser
from .table_detector import FNBTableDetector
from .transaction_builder import FNBTransactionBuilder
from .statement_period import detect_statement_period, write_period_debug_pdf, write_period_debug_json


class FNBStatementExtractor(BankStatementExtractor):
    """
    FNB adapter.

    Everything before FNBNormalizer is allowed to understand FNB-specific PDF
    coordinates, Cr/Dr notation and OCR quirks. Everything returned from this
    class uses WalletWise's common transaction model.
    """

    bank_name = "FNB"

    def __init__(self, app_config: AppConfig):
        self.app_config = app_config
        self.config = FNBConfig(tesseract_cmd=app_config.tesseract_cmd)

        self.row_parser = FNBRowParser(self.config)
        self.table_detector = FNBTableDetector(self.config, self.row_parser)
        self.pdf_extractor = FNBPDFExtractor(
            self.config,
            self.row_parser,
            self.table_detector,
        )
        self.ocr_service = FNBOCRService(self.config, self.table_detector)
        self.builder = FNBTransactionBuilder(self.config, self.table_detector)
        self.normalizer = FNBNormalizer()
        self.debugger = FNBDebugPDFGenerator(
            self.config,
            self.table_detector,
            self.ocr_service,
        )

    def extract(self, pdf_file: str) -> ExtractionResult:
        with pymupdf.open(pdf_file) as pdf:
            period = detect_statement_period(
                pdf, ocr_enabled=self.config.enable_ocr, tesseract_cmd=self.config.tesseract_cmd,
            )
        if period is None and self.config.statement_start_year is None:
            raise ValueError(
                "FNB Statement Period could not be verified in the PDF header. "
                "Import stopped to avoid assigning transactions to the wrong year. "
                "Check that the PDF includes 'Statement Period: <start> to <end>' "
                "and that OCR is available if it is a scanned statement."
            )
        start_year = period.start.year if period else self.config.statement_start_year
        if period:
            print(f"[PERIOD] Verified {period.start:%Y-%m-%d} -> {period.end:%Y-%m-%d} "
                  f"(page {period.page}, {period.source})")
        else:
            print(f"[PERIOD] Manual year override: {start_year} (UNVERIFIED)")

        rows, page_counts = self.pdf_extractor.extract_rows(pdf_file)
        print(f"Visual rows detected: {len(rows)}")

        debug_files = []
        if self.app_config.generate_debug_pdfs:
            table_debug = f"{self.app_config.debug_prefix}_FNB_Table_Debug.pdf"
            self.debugger.create_table_debug_pdf(pdf_file, table_debug)
            debug_files.append(table_debug)
            print(f"Created debug PDF: {table_debug}")

        rows, repaired = self.ocr_service.repair_missing_descriptions(pdf_file, rows)
        print(f"Descriptions repaired with OCR: {repaired}")

        if self.app_config.generate_debug_pdfs:
            ocr_debug = f"{self.app_config.debug_prefix}_FNB_OCR_Debug.pdf"
            if self.debugger.create_ocr_debug_pdf(pdf_file, rows, ocr_debug):
                debug_files.append(ocr_debug)

        drafts, orphan_rows = self.builder.build(rows, start_year, statement_period=period)
        print(f"[PERIOD] Accepted {len(drafts)} rows; rejected {len(orphan_rows)} unassigned rows")
        if period and self.app_config.generate_debug_pdfs:
            period_pdf = f"{self.app_config.debug_prefix}_FNB_Period_Debug.pdf"
            period_json = f"{self.app_config.debug_prefix}_FNB_Period_Debug.json"
            write_period_debug_pdf(pdf_file, period, period_pdf)
            write_period_debug_json(period, drafts, orphan_rows, period_json)
            debug_files.extend([period_pdf, period_json])
        transactions = self.normalizer.transactions(drafts)
        unassigned = self.normalizer.orphan_rows(orphan_rows)

        return ExtractionResult(
            bank=self.bank_name,
            transactions=transactions,
            page_counts=page_counts,
            unassigned_rows=unassigned,
            debug_files=debug_files,
            statement_period=period.as_dict() if period else None,
        )

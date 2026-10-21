import pymupdf

from walletwise.config import AppConfig
from walletwise.extraction.base import BankStatementExtractor
from walletwise.models import ExtractionResult
from walletwise.utils.date_utils import detect_statement_year

from .config import FNBConfig
from .debug_pdf import FNBDebugPDFGenerator
from .normalizer import FNBNormalizer
from .ocr_service import FNBOCRService
from .pdf_extractor import FNBPDFExtractor
from .row_parser import FNBRowParser
from .table_detector import FNBTableDetector
from .transaction_builder import FNBTransactionBuilder


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

    def _statement_year(self, pdf_file: str) -> int:
        if self.config.statement_start_year is not None:
            return self.config.statement_start_year
        with pymupdf.open(pdf_file) as pdf:
            return detect_statement_year(pdf)

    def extract(self, pdf_file: str) -> ExtractionResult:
        start_year = self._statement_year(pdf_file)
        print(f"Using statement year: {start_year}")

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

        drafts, orphan_rows = self.builder.build(rows, start_year)
        transactions = self.normalizer.transactions(drafts)
        unassigned = self.normalizer.orphan_rows(orphan_rows)

        return ExtractionResult(
            bank=self.bank_name,
            transactions=transactions,
            page_counts=page_counts,
            unassigned_rows=unassigned,
            debug_files=debug_files,
        )

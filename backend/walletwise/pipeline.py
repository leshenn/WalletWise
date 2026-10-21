from walletwise.config import AppConfig
from walletwise.extraction import BankDetector, ExtractorRegistry
from walletwise.output import ExcelExporter
from walletwise.processing import CategoryService, ConfidenceService, TransactionNamer, TransactionValidator


class WalletWisePipeline:
    """
    Bank-independent orchestration layer.

    It never knows FNB coordinates, OCR crop sizes, or Cr/Dr parsing rules.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.detector = BankDetector()
        self.namer = TransactionNamer()
        self.categorizer = CategoryService()
        self.validator = TransactionValidator()
        self.confidence = ConfidenceService()
        self.exporter = ExcelExporter()

    def run(self):
        print()
        print("WalletWise - reading bank statement...")

        bank = self.detector.detect(self.config.pdf_file)
        print(f"Detected bank: {bank}")

        extractor = ExtractorRegistry.create(bank, self.config)
        result = extractor.extract(self.config.pdf_file)

        # From this point onward the rest of WalletWise is bank-independent.
        self.namer.enrich(result.transactions)
        self.categorizer.classify(result.transactions)
        self.validator.validate(result.transactions)
        self.confidence.evaluate(result.transactions)

        counts = self.exporter.export(result, self.config.output_file)

        print(f"Transactions extracted: {len(result.transactions)}")
        print(f"High confidence: {counts.get('high', 0)}")
        print(f"Medium confidence: {counts.get('medium', 0)}")
        print(f"Low confidence: {counts.get('low', 0)}")
        print(f"Unassigned rows: {len(result.unassigned_rows)}")
        print()
        print(f"Created: {self.config.output_file}")

        return result

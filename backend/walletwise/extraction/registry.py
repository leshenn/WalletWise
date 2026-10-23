from walletwise.config import AppConfig
from walletwise.exceptions import UnsupportedBankError


class ExtractorRegistry:
    """Create the correct bank adapter after BankDetector identifies the bank."""

    SUPPORTED_BANKS = ("FNB", "Capitec")

    @classmethod
    def create(cls, bank: str, app_config: AppConfig):
        normalized = bank.strip().upper()

        if normalized == "FNB":
            # Imported lazily so bank-specific dependencies remain isolated.
            from walletwise.extraction.banks.fnb import FNBStatementExtractor

            return FNBStatementExtractor(app_config)

        if normalized == "CAPITEC":
            from walletwise.extraction.banks.capitec import CapitecStatementExtractor
            return CapitecStatementExtractor(app_config)

        raise UnsupportedBankError(
            f"No extractor is registered for {bank}. "
            f"Supported banks: {', '.join(cls.SUPPORTED_BANKS)}"
        )

from abc import ABC, abstractmethod

from walletwise.models import ExtractionResult


class BankStatementExtractor(ABC):
    """Contract every bank-specific adapter must implement."""

    bank_name: str

    @abstractmethod
    def extract(self, pdf_file: str) -> ExtractionResult:
        """Return bank-independent WalletWise transactions."""
        raise NotImplementedError

class WalletWiseError(Exception):
    """Base WalletWise exception."""


class UnsupportedBankError(WalletWiseError):
    """Raised when WalletWise has no extractor for the detected statement."""

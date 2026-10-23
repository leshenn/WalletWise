import re

import pymupdf

from walletwise.exceptions import UnsupportedBankError


class BankDetector:
    """
    Detect the statement's bank before extraction.

    FNB and Capitec signatures are recognised; never guess unknown banks.
    """

    @staticmethod
    def _first_pages_text(pdf_file: str, page_limit: int = 2) -> str:
        with pymupdf.open(pdf_file) as pdf:
            chunks = []
            for page in list(pdf)[:page_limit]:
                chunks.append(page.get_text())
        return "\n".join(chunks).lower()

    def detect(self, pdf_file: str) -> str:
        text = self._first_pages_text(pdf_file)

        if ("capitec bank limited" in text or "capitec bank is an authorised" in text or
                ("capitec" in text and "main account statement" in text)):
            return "Capitec"

        # Strong FNB identifiers. We intentionally do not guess another bank.
        fnb_signatures = (
            "first national bank",
            "www.fnb.co.za",
            "fnb app",
        )

        if any(signature in text for signature in fnb_signatures):
            return "FNB"

        # Some statements may use FNB prominently without the full legal name.
        if re.search(r"\bfnb\b", text) and "account" in text:
            return "FNB"

        raise UnsupportedBankError(
            "WalletWise could not identify a supported bank statement. "
            "Supported banks: FNB and Capitec."
        )

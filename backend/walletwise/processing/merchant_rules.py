"""Known merchant rules used by WalletWise's bank-independent naming layer.

Keep bank-specific parsing out of this file.  These rules operate on the common
WalletWise ``raw_description`` after a bank adapter has extracted it.

Add more rules over time as you encounter real descriptions.  Put more specific
rules before broader ones (for example Uber Eats before Uber).
"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MerchantRule:
    name: str
    category: str
    patterns: tuple[str, ...]

    def matches(self, description: str) -> bool:
        return any(re.search(pattern, description, flags=re.IGNORECASE) for pattern in self.patterns)


MERCHANT_RULES: tuple[MerchantRule, ...] = (
    # Groceries / retail
    MerchantRule("Checkers", "Groceries", (r"\bcheckers\b", r"\bcheckers\s+hyper\b")),
    MerchantRule("Shoprite", "Groceries", (r"\bshoprite\b",)),
    MerchantRule("Pick n Pay", "Groceries", (r"\bpick\s*n\s*pay\b", r"\bpnp\b")),
    MerchantRule("Woolworths", "Groceries", (r"\bwoolworths\b", r"\bwoolies\b")),
    MerchantRule("SPAR", "Groceries", (r"\bspar\b",)),

    # Online / subscriptions
    MerchantRule("Takealot", "Shopping", (r"\btakealot\b",)),
    MerchantRule("Amazon", "Shopping", (r"\bamazon\b",)),
    MerchantRule("Netflix", "Subscriptions", (r"\bnetflix\b",)),
    MerchantRule("Spotify", "Subscriptions", (r"\bspotify\b",)),

    # Transport / delivery
    MerchantRule("Uber Eats", "Eating Out", (r"\buber\s*eats\b", r"\bubereats\b")),
    MerchantRule("Mr D", "Eating Out", (r"\bmr\s*d\b", r"\bmrd\b")),
    MerchantRule("Uber", "Transport", (r"\buber\b",)),
    MerchantRule("Bolt", "Transport", (r"\bbolt\b",)),

    # Fuel
    MerchantRule("Engen", "Fuel", (r"\bengen\b",)),
    MerchantRule("Shell", "Fuel", (r"\bshell\b",)),
    MerchantRule("BP", "Fuel", (r"\bbp\b",)),
    MerchantRule("Sasol", "Fuel", (r"\bsasol\b",)),

    # Pharmacy / health retail
    MerchantRule("Clicks", "Health & Personal Care", (r"\bclicks\b",)),
    MerchantRule("Dis-Chem", "Health & Personal Care", (r"\bdis[\s-]*chem\b",)),

    # Restaurants / fast food
    MerchantRule("McDonald's", "Eating Out", (r"\bmcdonald'?s\b", r"\bmcd\b")),
    MerchantRule("KFC", "Eating Out", (r"\bkfc\b",)),
    MerchantRule("Nando's", "Eating Out", (r"\bnando'?s\b",)),
)

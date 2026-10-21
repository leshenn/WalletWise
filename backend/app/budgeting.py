from __future__ import annotations

from statistics import median


class BudgetService:
    """Build a first-draft budget from necessities, real history and user priorities.

    The budgeting order is intentionally simple:
      1. Protect necessities first.
      2. Suggest a savings target and safety buffer from what remains.
      3. Fit flexible spending into the remaining room.

    Explicit necessity estimates are the strongest signal. A value of 0 means
    the user does not have that expense. A missing value lets WalletWise infer
    a starting amount from recent complete-month history.
    """

    DEFAULT_PROFILE = {
        "livingSituation": "family",
        "householdResponsibility": "self",
        "transportStyle": "car",
        "goal": "emergency",
        "budgetStyle": "balanced",
        "incomeStability": "stable",
        "emergencyFundLevel": "none",
        "savingsTargetAmount": None,
        "expenseEstimates": {},
    }

    BUCKETS = [
        {
            "key": "housing",
            "label": "Housing",
            "categories": {"Housing", "Rent", "Bond"},
            "group": "essential",
        },
        {
            "key": "groceries",
            "label": "Groceries",
            "categories": {"Groceries"},
            "group": "essential",
        },
        {
            "key": "transport",
            "label": "Transport & fuel",
            "categories": {"Transport", "Fuel"},
            "group": "essential",
        },
        {
            "key": "utilities",
            "label": "Bills & utilities",
            "categories": {"Bills", "Bills & Utilities", "Utilities"},
            "group": "essential",
        },
        {
            "key": "mobileInternet",
            "label": "Mobile, data & internet",
            "categories": {"Mobile & Internet", "Airtime & Data", "Phone & Internet", "Airtime", "Data"},
            "group": "essential",
        },
        {
            "key": "insurance",
            "label": "Insurance",
            "categories": {"Insurance"},
            "group": "essential",
        },
        {
            "key": "healthcare",
            "label": "Healthcare",
            "categories": {"Health", "Healthcare"},
            "group": "essential",
        },
        {
            "key": "debtRepayments",
            "label": "Debt repayments",
            "categories": {"Debt", "Debt Repayments", "Loans", "Credit"},
            "group": "essential",
        },
        {
            "key": "toiletries",
            "label": "Toiletries & personal care",
            "categories": {"Toiletries", "Personal Care", "Health & Personal Care"},
            "group": "essential",
        },
        {
            "key": "familySupport",
            "label": "Family support",
            "categories": {"Family Support"},
            "group": "essential",
        },
        {
            "key": "educationChildcare",
            "label": "Education / childcare",
            "categories": {"Education", "Childcare"},
            "group": "essential",
        },
        {
            "key": "otherEssentials",
            "label": "Other necessities",
            "categories": {"Bank Fees", "Other Essentials"},
            "group": "essential",
        },
        {
            "key": "subscriptions",
            "label": "Subscriptions",
            "categories": {"Subscriptions"},
            "group": "flexible",
        },
        {
            "key": "eatingOut",
            "label": "Eating out",
            "categories": {"Eating Out"},
            "group": "flexible",
        },
        {
            "key": "shoppingPersonal",
            "label": "Shopping & wants",
            "categories": {"Shopping"},
            "group": "flexible",
        },
        {
            "key": "entertainment",
            "label": "Entertainment",
            "categories": {"Entertainment"},
            "group": "flexible",
        },
    ]

    @classmethod
    def suggest(cls, monthly: list[dict], profile: dict | None) -> dict:
        profile_completed = bool(profile)
        profile = cls._normalise_profile(profile)
        usable = [m for m in monthly if (m.get("coverageStatus") or "complete") != "partial"] or monthly

        income_values = [float(m.get("income") or 0) for m in usable if float(m.get("income") or 0) > 0]
        income_basis = round(median(income_values), 2) if income_values else 0.0

        bucket_averages = cls._recent_bucket_averages(usable)
        bucket_actuals = cls._bucket_actuals(monthly)
        estimates = profile.get("expenseEstimates") or {}

        essential_items: list[dict] = []
        flexible_items: list[dict] = []

        for bucket in cls.BUCKETS:
            key = bucket["key"]
            recent_average = bucket_averages.get(key, 0.0)
            estimate_present = key in estimates and estimates.get(key) is not None
            estimate = cls._number(estimates.get(key)) if estimate_present else None

            if bucket["group"] == "essential":
                if estimate_present:
                    budgeted = max(0.0, estimate)
                    source = "Your estimate"
                else:
                    budgeted = cls._adjust_essential_amount(bucket, recent_average, profile)
                    source = "Recent spending"
                essential_items.append(cls._item(bucket, budgeted, recent_average, estimate, source, bucket_actuals))
            else:
                # Flexible categories are suggested after necessities, savings and
                # the safety buffer are known. Legacy explicit values are still
                # respected if they exist in an older saved profile.
                if estimate_present:
                    budgeted = max(0.0, estimate)
                    source = "Your estimate"
                else:
                    budgeted = cls._adjust_flexible_amount(recent_average, profile)
                    source = "Recent spending"
                flexible_items.append(cls._item(bucket, budgeted, recent_average, estimate, source, bucket_actuals))

        essential_budget = round(sum(float(item["budgeted"]) for item in essential_items), 2)
        available_after_essentials = max(0.0, income_basis - essential_budget)

        desired_savings, savings_source, desired_savings_pct = cls._savings_target(income_basis, profile)
        desired_buffer = income_basis * (0.08 if profile["incomeStability"] == "variable" else 0.05) if income_basis else 0.0

        # Necessities are protected first. The buffer is kept modest when the
        # essentials already consume most of the income, then savings is capped
        # to the money that really remains.
        buffer_target = min(desired_buffer, available_after_essentials * 0.25)
        savings_target = min(desired_savings, max(0.0, available_after_essentials - buffer_target))
        if savings_target < desired_savings and desired_savings > 0:
            savings_source = "WalletWise suggestion, adjusted to fit necessities"

        available_for_flexible = max(0.0, income_basis - essential_budget - savings_target - buffer_target)
        flexible_items = cls._fit_flexible_items(flexible_items, available_for_flexible)
        flexible_budget = round(sum(float(item["budgeted"]) for item in flexible_items), 2)

        items = essential_items + flexible_items
        planned_expenses = round(essential_budget + flexible_budget, 2)
        total_plan = round(planned_expenses + savings_target + buffer_target, 2)
        remaining_after_plan = round(income_basis - total_plan, 2) if income_basis else 0.0

        savings_recent = cls._average([float(m.get("netSavingsMovement") or 0) for m in usable])
        savings_actual_by_month = {
            str(m.get("month")): round(float(m.get("netSavingsMovement") or 0), 2)
            for m in monthly if m.get("month")
        }
        savings_pct = (savings_target / income_basis * 100) if income_basis else desired_savings_pct
        items.append({
            "key": "savings",
            "category": "Savings",
            "suggested": cls._round10(savings_target),
            "budgeted": cls._round10(savings_target),
            "recentAverage": savings_recent,
            "estimate": None,
            "source": savings_source,
            "kind": "savings",
            "group": "savings",
            "flexible": False,
            "actualByMonth": savings_actual_by_month,
        })
        items.append({
            "key": "buffer",
            "category": "Breathing room",
            "suggested": cls._round10(buffer_target),
            "budgeted": cls._round10(buffer_target),
            "recentAverage": 0.0,
            "estimate": None,
            "source": "WalletWise safety buffer",
            "kind": "buffer",
            "group": "buffer",
            "flexible": False,
            "actualByMonth": {},
        })

        return {
            "profile": profile,
            "profileCompleted": profile_completed,
            "incomeBasis": income_basis,
            "essentialBudget": essential_budget,
            "flexibleBudget": flexible_budget,
            "plannedExpenses": planned_expenses,
            "savingsTarget": round(savings_target, 2),
            "savingsTargetPct": round(savings_pct, 1),
            "bufferTarget": round(buffer_target, 2),
            "totalPlanned": total_plan,
            "remainingAfterPlan": remaining_after_plan,
            "overBudget": bool(income_basis and essential_budget > income_basis),
            "items": items,
            "availableActualPeriods": [
                {"month": m.get("month"), "label": m.get("label")}
                for m in monthly if m.get("month")
            ],
            "method": (
                "Necessities are protected first. WalletWise then suggests savings, a safety buffer and flexible spending "
                "from what remains. Enter 0 when a necessity does not apply; leave it blank to use recent spending."
            ),
        }

    @classmethod
    def _item(
        cls,
        bucket: dict,
        budgeted: float,
        recent_average: float,
        estimate: float | None,
        source: str,
        bucket_actuals: dict[str, dict[str, float]],
    ) -> dict:
        rounded = cls._round10(max(0.0, budgeted))
        return {
            "key": bucket["key"],
            "category": bucket["label"],
            "suggested": rounded,
            "budgeted": rounded,
            "recentAverage": round(recent_average, 2),
            "estimate": round(estimate, 2) if estimate is not None else None,
            "source": source,
            "kind": "expense",
            "group": bucket["group"],
            "flexible": bucket["group"] == "flexible",
            "actualByMonth": bucket_actuals.get(bucket["key"], {}),
        }

    @classmethod
    def _normalise_profile(cls, profile: dict | None) -> dict:
        raw = {**cls.DEFAULT_PROFILE, **(profile or {})}
        raw["expenseEstimates"] = dict(raw.get("expenseEstimates") or {})
        return raw

    @classmethod
    def _recent_bucket_averages(cls, monthly: list[dict]) -> dict[str, float]:
        if not monthly:
            return {bucket["key"]: 0.0 for bucket in cls.BUCKETS}

        totals: dict[str, list[float]] = {bucket["key"]: [] for bucket in cls.BUCKETS}
        for month in monthly:
            by_category = {
                item.get("name"): float(item.get("amount") or 0)
                for item in month.get("categoryBreakdown", [])
                if item.get("name")
            }
            for bucket in cls.BUCKETS:
                amount = sum(by_category.get(name, 0.0) for name in bucket["categories"])
                totals[bucket["key"]].append(amount)

        return {
            key: round(sum(values) / len(values), 2) if values else 0.0
            for key, values in totals.items()
        }

    @classmethod
    def _bucket_actuals(cls, monthly: list[dict]) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {bucket["key"]: {} for bucket in cls.BUCKETS}
        for month in monthly:
            month_key = str(month.get("month") or "")
            if not month_key:
                continue
            by_category = {
                item.get("name"): float(item.get("amount") or 0)
                for item in month.get("categoryBreakdown", [])
                if item.get("name")
            }
            for bucket in cls.BUCKETS:
                result[bucket["key"]][month_key] = round(
                    sum(by_category.get(name, 0.0) for name in bucket["categories"]), 2
                )
        return result

    @classmethod
    def _adjust_essential_amount(cls, bucket: dict, amount: float, profile: dict) -> float:
        value = max(0.0, amount)
        key = bucket["key"]
        if key == "groceries":
            value *= {"self": 1.0, "share": 1.08, "support": 1.18}.get(profile["householdResponsibility"], 1.0)
        elif key == "transport":
            value *= {"car": 1.08, "public": 0.95, "ride": 1.12, "remote": 0.80}.get(profile["transportStyle"], 1.0)
        elif key == "utilities":
            value *= {"family": 0.95, "rent": 1.08, "own": 1.12}.get(profile["livingSituation"], 1.0)
        return value

    @classmethod
    def _adjust_flexible_amount(cls, amount: float, profile: dict) -> float:
        value = max(0.0, amount)
        return value * {"conservative": 0.78, "balanced": 0.92, "flexible": 1.02}.get(profile["budgetStyle"], 0.92)

    @classmethod
    def _savings_target(cls, income_basis: float, profile: dict) -> tuple[float, str, float]:
        explicit = profile.get("savingsTargetAmount")
        if explicit is not None:
            amount = max(0.0, cls._number(explicit))
            pct = (amount / income_basis * 100) if income_basis else 0.0
            return round(amount, 2), "Your savings target", pct

        savings_pct = {"conservative": 0.22, "balanced": 0.18, "flexible": 0.12}.get(profile["budgetStyle"], 0.18)
        savings_pct += {"emergency": 0.05, "purchase": 0.04, "debt": -0.03, "breathing": 0.0}.get(profile["goal"], 0.0)
        savings_pct += {"none": 0.04, "under_one": 0.03, "one_to_three": 0.01, "three_plus": 0.0}.get(profile["emergencyFundLevel"], 0.0)
        if profile["incomeStability"] == "variable":
            savings_pct += 0.03
        savings_pct = max(0.05, min(0.40, savings_pct))
        amount = income_basis * savings_pct if income_basis else 0.0
        return round(amount, 2), "WalletWise suggestion", savings_pct * 100

    @classmethod
    def _fit_flexible_items(cls, items: list[dict], available: float) -> list[dict]:
        if not items:
            return items

        current = sum(float(item.get("budgeted") or 0) for item in items)
        if current <= available:
            return items
        if available <= 0:
            for item in items:
                if item.get("source") != "Your estimate":
                    item["budgeted"] = 0.0
                    item["suggested"] = 0.0
                    item["source"] = "Reduced after necessities and savings"
            return items

        fixed = [item for item in items if item.get("source") == "Your estimate"]
        inferred = [item for item in items if item.get("source") != "Your estimate"]
        fixed_total = sum(float(item.get("budgeted") or 0) for item in fixed)
        room_for_inferred = max(0.0, available - fixed_total)
        inferred_total = sum(float(item.get("budgeted") or 0) for item in inferred)
        scale = min(1.0, room_for_inferred / inferred_total) if inferred_total else 0.0

        for item in inferred:
            item["budgeted"] = cls._round10(float(item.get("budgeted") or 0) * scale)
            item["suggested"] = item["budgeted"]
            if scale < 1:
                item["source"] = "Recent spending, adjusted to fit your plan"
        return items

    @staticmethod
    def _number(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _round10(value: float) -> float:
        return round(float(value) / 10) * 10

    @staticmethod
    def _average(values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

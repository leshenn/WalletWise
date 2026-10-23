from __future__ import annotations

from pydantic import BaseModel, Field


class TransactionUpdate(BaseModel):
    displayName: str | None = None
    category: str | None = None
    categoryConfidence: str | None = None
    categoryReason: str | None = None
    financialRole: str | None = None
    savingsDirection: str | None = Field(default=None, pattern='^(into|out|none|auto)$')
    roleConfidence: str | None = None
    roleReason: str | None = None
    namingConfidence: str | None = None
    nameReason: str | None = None
    reviewReasons: list[str] | None = None
    humanVerified: bool | None = None
    isSalary: bool | None = None
    salaryExcluded: bool | None = None


class NamingRulePayload(BaseModel):
    matchText: str = Field(min_length=1)
    displayName: str = Field(min_length=1)
    category: str = "Uncategorised"
    matchType: str = "contains"
    scope: str = "global"
    active: bool = True


class CategoryPayload(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class CategoryRulePayload(BaseModel):
    matchText: str = Field(min_length=1)
    category: str = Field(min_length=1)
    matchType: str = "contains"
    confidence: str = "high"
    priority: int = 50
    active: bool = True


class ReviewConfirmationPayload(BaseModel):
    transactionIds: list[str] = Field(min_length=1)
    financialRole: str = Field(min_length=1)
    category: str = "Uncategorised"
    displayName: str | None = None


class BatchTransactionUpdate(BaseModel):
    transactionIds: list[str] = Field(min_length=1)
    displayName: str | None = None
    category: str | None = None
    categoryConfidence: str | None = None
    categoryReason: str | None = None
    financialRole: str | None = None
    savingsDirection: str | None = Field(default=None, pattern='^(into|out|none|auto)$')
    roleConfidence: str | None = None
    roleReason: str | None = None
    namingConfidence: str | None = None
    nameReason: str | None = None
    humanVerified: bool | None = None
    isSalary: bool | None = None
    salaryExcluded: bool | None = None


class SalaryMarkerPayload(BaseModel):
    isSalary: bool


class TrackingSettingsPayload(BaseModel):
    defaultTrackingView: str | None = None
    expectedPayday: int | None = Field(default=None, ge=1, le=31)


class SalarySourceConfirmationPayload(BaseModel):
    transactionId: str = Field(min_length=1)
    selectedTransactionIds: list[str] | None = None



class BudgetProfilePayload(BaseModel):
    livingSituation: str
    householdResponsibility: str
    transportStyle: str
    goal: str
    budgetStyle: str
    incomeStability: str
    emergencyFundLevel: str = "none"
    savingsTargetAmount: float | None = Field(default=None, ge=0)
    expenseEstimates: dict[str, float | None] = Field(default_factory=dict)

class ReportExplorerPayload(BaseModel):
    startDate: str | None = None
    endDate: str | None = None
    category: str | None = None
    merchant: str | None = None
    financialRole: str | None = None


class AIHistoryMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class AIAskPayload(BaseModel):
    bank: str | None = None  # all / FNB / Capitec; server validates
    question: str = Field(min_length=2, max_length=2000)
    history: list[AIHistoryMessage] = Field(default_factory=list, max_length=4)
    conversationId: str | None = Field(default=None, max_length=80)


class AIChatRenamePayload(BaseModel):
    title: str = Field(min_length=1, max_length=80)

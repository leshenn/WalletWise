# WalletWise frontend/backend contract

Transactions returned by the Python backend use the standard WalletWise shape. Important fields include:

```json
{
  "id": "tx-123",
  "date": "2026-10-20",
  "displayName": "Checkers",
  "rawDescription": "POS Purchase Checkers ...",
  "financialRole": "expense",
  "roleConfidence": "high",
  "category": "Groceries",
  "categoryConfidence": "high",
  "amount": -250.0,
  "transactionType": "debit",
  "balance": 1000.0,
  "bank": "FNB",
  "extractionConfidence": "high",
  "namingConfidence": "high",
  "humanVerified": true,
  "isSalary": false,
  "canBeSalaryAnchor": false,
  "sourceStatementCount": 1
}
```

`transactionType` describes the bank direction (`debit` / `credit`).
`financialRole` describes the actual financial meaning (`expense`, `income`, `transfer`, `refund`).

`canBeSalaryAnchor` is calculated by the backend. React only uses it to decide whether to display the “Mark salary” action; the backend still validates the action.

## Statement history

`GET /api/statements` returns imported source statements. `POST /api/statements/process` appends instead of replacing history. `DELETE /api/statements/{id}` removes one source statement while preserving transactions still linked to another statement.

`GET /api/coverage` returns backend-calculated estimated coverage by calendar month.

## Tracking

`GET/PUT /api/tracking-settings` stores the default report view and optional expected payday.

`PATCH /api/transactions/{id}/salary` marks an eligible income transaction as an actual salary/pay-cycle anchor.

`GET /api/reports` returns both `monthly` and `payCycles`. `GET /api/reports/custom` calculates an arbitrary date-range report on the backend.

## Review

Review groups are returned by `GET /api/review-groups` in four priority buckets:

- `roleReview`
- `uncategorised`
- `categoryReview`
- `nameReview`

The frontend submits user choices to `POST /api/review/confirm`; confidence and review-state business rules stay in Python.

## Income and salary detection

### `GET /api/income-overview`
Returns confirmed salary sources, likely salary suggestions, and other incoming-money groups. Candidate scoring is backend-owned.

### `POST /api/income/salary-source`
Body:
```json
{ "transactionId": "tx-..." }
```
Confirms the incoming transaction's source as salary, marks matching historical income as salary anchors, persists the learned source, and returns the refreshed income overview.

## Reporting additions

`GET /api/reports` now also returns:

- `availableMonths`
- `categoryTrend`
- richer `changeFromPrevious` objects containing backend-calculated previous/current period values and deltas.

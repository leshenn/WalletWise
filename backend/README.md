# WalletWise Python backend

This backend exposes the existing WalletWise extraction engine through FastAPI.

## Responsibilities

- accepts a PDF statement
- detects the bank
- uses the registered bank extractor (FNB only right now)
- performs OCR fallback when required
- normalises transactions into the common WalletWise schema
- applies naming conventions from SQLite
- validates/confidence-scores transactions
- persists an append-only statement history, canonical transactions, rules and tracking settings in SQLite
- exposes transactions/rules to the React frontend

The uploaded PDF is written to a temporary local file only while it is being processed, then deleted.

## Run on Windows

From the `backend` folder:

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

API: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs`

The server binds to `0.0.0.0`, so the frontend can also reach it from a phone on the same Wi-Fi.


## Statement history

`POST /api/statements/process` appends a statement. Overlapping transaction rows are de-duplicated and linked to every source statement that contains them. `DELETE /api/statements/{id}` removes a source statement without removing transactions that are still backed by another imported statement.

Pay-cycle reports are created from transactions explicitly marked as salary; an expected payday is only a UI/reporting hint.

## Wally semantic routing

Wally now builds and validates a semantic query plan before answering. Ambiguous natural-language phrasing may use a tiny language-only Groq planning call with no financial data. Local answers are validated against the plan before being returned. `WALLETWISE_TRACE=true` enables the single unified routing trace.

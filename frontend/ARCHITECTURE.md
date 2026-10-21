# Frontend architecture

## `src/App.jsx`
Loads the current dataset, categories and naming rules from the Python API. There is no mock dataset or localStorage data layer.

## `src/pages/`
- `DashboardPage` - overview derived from live standardised transactions
- `UploadPage` - uploads a real PDF to FastAPI
- `TransactionsPage` - browser for the standard WalletWise schema
- `ReviewPage` - saves user corrections to SQLite through the API
- `RulesPage` - CRUD for naming conventions stored in SQLite
- `ReportsPage` - calculates reports from the current live dataset

## `src/services/api.js`
Single HTTP boundary. It automatically uses `http://<current-host>:8000/api`, so opening the Vite site from a phone also points back to the computer running Python.

The frontend remains bank-independent. FNB coordinates, OCR behaviour and `Cr/Dr` parsing stay exclusively in the Python extraction adapter.

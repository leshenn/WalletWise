# WalletWise connected frontend MVP

The mock dataset has been removed. This React/Vite frontend now talks directly to the Python/FastAPI backend.

## Run

Start the backend first, then from this `frontend` folder:

```cmd
npm install
npm run dev
```

The Vite server already listens on your LAN. On your computer use the `Local` URL. On your phone, while connected to the same Wi-Fi, use the `Network` URL printed by Vite.

The frontend automatically calls port `8000` on the same hostname it was opened from. This means:

- PC: `http://localhost:5173` -> `http://localhost:8000/api`
- Phone: `http://192.168.x.x:5173` -> `http://192.168.x.x:8000/api`

No `.env` is required for the normal local setup.

## Live features

- append multiple real FNB PDF statements
- display actual extracted transactions
- review uncertain names/categories
- persist transaction corrections in SQLite
- create/edit/delete naming conventions
- immediately apply new/edited naming rules to matching current transactions
- manage/delete imported statements and see estimated missing-month coverage
- mark actual salary deposits as pay-cycle anchors
- compare calendar months, salary-to-salary cycles, or a custom date range
- reports calculated by the Python backend

The frontend contains no mock transaction data.

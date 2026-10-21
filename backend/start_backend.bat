@echo off
if not exist .env (
  copy .env.example .env >nul
  echo Created backend\.env from .env.example. Add GROQ_API_KEY there to enable Ask WalletWise.
)
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
python run.py

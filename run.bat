@echo off
REM One-command launcher for Windows.
REM Installs deps, prepares the dataset on first run, then starts the server.
setlocal
cd /d "%~dp0"

echo [1/3] Installing dependencies...
python -m pip install -q -r requirements.txt

if not exist data\queries.csv (
  echo [2/3] No dataset found - generating synthetic dataset of 120k queries...
  python -m scripts.generate_dataset --rows 120000
) else (
  echo [2/3] Dataset already present - skipping generation.
)

if not exist data\typeahead.db (
  echo       Ingesting dataset into SQLite...
  python -m scripts.ingest
) else (
  echo       SQLite store already present - skipping ingest.
)

echo [3/3] Starting server at http://127.0.0.1:8000  (Ctrl+C to stop)
python -m uvicorn app.main:app --port 8000

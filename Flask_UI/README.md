# FlaskUI — Mist AP Site Import (Upload + Site Select)

This is a small Flask web UI that:
1) lets you upload a CSV or XLSX file
2) lets you select a target Mist site (dropdown populated via Mist API)
3) assigns APs (matched by **Serial Number**) to that site via Mist Inventory API
4) shows results in the browser and provides a downloadable results CSV

## Prereqs
- A Mist API token with access to your Org
- Your Org ID
- Correct regional API base URL (example: portal `manage.ac2.mist.com` → `https://api.ac2.mist.com`)

## Input file requirements
CSV or XLSX with these columns:
- `Floor #`
- `WAP Hostname`
- `Serial Number`
- `Mac Address`

The script matches on **Serial Number**. MAC from inventory is used (falls back to file if needed).

## Setup (config.ini)
Copy the template and edit it:

```bash
cp config.ini.template config.ini
```

Example:

```ini
[mist]
base_url = https://api.ac2.mist.com
api_token = YOUR_API_TOKEN
org_id = YOUR_ORG_ID
```

## Run with Docker Compose (recommended)
From the folder containing `docker-compose.yml`:

```bash
cp config.ini.template config.ini
# edit config.ini
docker compose up --build
```

Then open:
- http://localhost:8080

Notes:
- `config.ini` is mounted read-only into the container
- `./data` is mounted so uploads/results persist on the host

## Run locally (no Docker)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open:
- http://127.0.0.1:8080

## Output
- Results are written under `data/results/` as `results_<timestamp>.csv`
- The Results page provides a download link

## Troubleshooting
- If site list fails: double-check `base_url` region and token permissions.
- If XLSX fails to read: confirm it is a real `.xlsx` workbook (not a CSV renamed to .xlsx).


## Validation behavior (all-or-none)
- Headers are case-insensitive; surrounding whitespace is ignored.
- Row values are stripped of whitespace.
- The import is **all-or-none**: if any row has an issue (e.g., missing Serial Number, serial not in inventory), **no APs are assigned**.
- The UI will show a table of every problem row so you can fix the file in one pass.


## Validation report download
When validation fails, the UI provides a **Download validation report (CSV)** link. This report includes the uploaded data plus `issue`, `issue_field`, and `issue_value` columns.

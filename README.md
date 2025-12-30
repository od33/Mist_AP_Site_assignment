# Mist AP Site Import

Tools to assign Juniper Mist Access Points (APs) to sites using inventory **Serial Number** matching.

This project provides **two interfaces** backed by the same validation and assignment logic:

- **CLI tool** – interactive command-line workflow
- **Flask Web UI** – browser-based upload and site selection
- **Docker Compose support** for the Flask UI

---

## Key Features

- ✅ Case-insensitive column handling
- ✅ Whitespace normalization for all fields
- ✅ **All-or-none imports** (no partial assignments)
- ✅ Pre-validation before any API calls
- ✅ Clear error reporting (row, field, value, reason)
- ✅ Downloadable **validation report CSV** on failure
- ✅ Safe Git hygiene (secrets and data excluded)

---

## Expected Input File Format

CSV or XLSX with the following **header row** (case-insensitive, whitespace ignored):

| Column Name     | Description |
|-----------------|-------------|
| Floor #         | Floor identifier (free text) |
| WAP Hostname    | Desired AP hostname |
| Serial Number   | **Required** – used to match inventory |
| Mac Address     | Optional (validated if present) |

Notes:
- Serial Number is the authoritative identifier
- MAC address from Mist inventory is used when assigning
- Extra columns are ignored

---

## All-or-None Validation Behavior

Before any AP is assigned:

1. Entire file is parsed and normalized
2. Headers are validated
3. Every row is checked:
   - Serial Number present
   - Serial exists in Mist org inventory
   - MAC format valid (if provided)

If **any** row fails validation:
- ❌ **No APs are assigned**
- ❌ Import is blocked
- ✅ A **validation report CSV** is generated listing *all* problems

This ensures users can fix the file **once** and re-upload confidently.

---

## Mist API Requirements

You will need:

- A Mist **API token**
- Your **Org ID**
- The correct **regional API base URL**

### Common Regions

| Portal URL | API Base URL |
|-----------|--------------|
| manage.ac2.mist.com | https://api.ac2.mist.com |
| manage.gc3.mist.com | https://api.gc3.mist.com |
| manage.mist.com | https://api.mist.com |

### Create an API Token

1. Log into the Mist portal
2. Go to **Organization → Admin → Settings**
3. Create an **API Token**
4. Copy the token (shown only once)

---

## Configuration

Create a `config.ini` file from the template:

```bash
cp config.ini.template config.ini
```

Example:

```ini
[mist]
base_url = https://api.ac2.mist.com
api_token = YOUR_API_TOKEN
org_id = YOUR_ORG_ID

# Optional: if XLSX data is on a specific sheet
xlsx_sheet_name =
```

⚠️ `config.ini` must **never** be committed to Git.

---

## CLI Usage

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pandas requests openpyxl
```

### Run

```bash
python mist_assign_aps.py
```

Workflow:
1. Enter CSV/XLSX filename
2. Select target site
3. File is validated (all-or-none)
4. APs are assigned or validation report is generated

Outputs:
- `*_results_<timestamp>.csv`
- `validation_report_<timestamp>.csv` (on failure)
- `ap_site_import.log`

---

## Flask Web UI (Docker Compose)

The Flask UI provides:
- File upload (CSV/XLSX)
- Site selection dropdown
- Validation error table
- Downloadable results or validation report

### Prerequisites
- Docker
- Docker Compose v2

### Run

```bash
cp config.ini.template config.ini
# edit config.ini

docker compose up --build
```

Open in browser:

```
http://localhost:8080
```

### Notes
- `config.ini` is mounted read-only into the container
- Uploaded files and reports are stored under `./data/`
- Validation failures do **not** perform any assignments

Stop the service:

```bash
docker compose down
```

---

## Git Safety (.gitignore)

The following must **never** be committed:

- `config.ini`
- `*.csv`
- `*.xlsx`
- logs
- virtual environments

---

## Disclaimer

This project is **not affiliated with or endorsed by Juniper Networks**.

Use at your own risk. Always test in a non-production org first.

#!/usr/bin/env python3
"""
Mist AP Site Import (CLI)

What it does:
- Prompts for a CSV/XLSX file containing AP info
- Prompts for a target Mist site
- Assigns APs to the selected site using the Mist Inventory API (match on Serial Number)
- Writes a new results file and logs to ap_site_import.log

Key behaviors (matches the latest Flask backend logic):
- Case-insensitive column matching (headers are normalized)
- Whitespace is stripped from headers and values
- ALL-OR-NONE: validates the entire file first; if any row has an issue, **no APs are assigned**
- When validation fails, creates a downloadable-style CSV report:
  validation_report_<timestamp>.csv
  (includes input data + issue columns so you can fix in one pass)

Expected fields (header row, case-insensitive):
- Floor #
- WAP Hostname
- Serial Number
- Mac Address
"""

import os
import sys
import logging
import configparser
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import requests

LOG_FILE = "ap_site_import.log"
REQUIRED_COLUMNS = ["floor #", "wap hostname", "serial number", "mac address"]


@dataclass
class RowIssue:
    row: int            # 1-based row number (excluding header)
    field: str
    value: str
    message: str


class ValidationError(Exception):
    def __init__(self, issues: List[RowIssue], message: str = "Validation failed"):
        super().__init__(message)
        self.issues = issues


def setup_logging() -> None:
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # Also log to stdout for convenience
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(console)


def die(msg: str, code: int = 1) -> None:
    logging.error(msg)
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config() -> Dict[str, str]:
    c = configparser.ConfigParser()
    if not os.path.exists("config.ini"):
        die("config.ini not found (copy config.ini.template to config.ini and set values)")

    c.read("config.ini")
    try:
        token = c["mist"]["api_token"].strip()
        org_id = c["mist"]["org_id"].strip()
        base_url = c["mist"]["base_url"].strip().rstrip("/")
        xlsx_sheet_name = c["mist"].get("xlsx_sheet_name", "").strip()
    except KeyError:
        die("config.ini missing required [mist] values")

    if not token or not org_id or not base_url:
        die("config.ini has empty [mist] values (api_token/org_id/base_url)")

    return {
        "token": token,
        "org_id": org_id,
        "base_url": base_url,
        "xlsx_sheet_name": xlsx_sheet_name,
    }


def headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
    }


def get_sites(cfg: Dict[str, str]) -> List[Dict[str, Any]]:
    url = f"{cfg['base_url']}/api/v1/orgs/{cfg['org_id']}/sites"
    r = requests.get(url, headers=headers(cfg["token"]), timeout=30)
    if not r.ok:
        die(f"Failed to list sites (HTTP {r.status_code}): {r.text}")
    sites = r.json()
    sites.sort(key=lambda s: (s.get("name") or "").lower())
    return sites


def choose_site(sites: List[Dict[str, Any]]) -> Dict[str, Any]:
    print("\nAvailable Sites:")
    for i, s in enumerate(sites, 1):
        print(f"{i}. {s.get('name')}")

    while True:
        val = input("Select target site number: ").strip()
        try:
            idx = int(val)
            if idx < 1 or idx > len(sites):
                raise ValueError()
            return sites[idx - 1]
        except Exception:
            print("Invalid selection. Try again.")


def get_inventory(cfg: Dict[str, str]) -> List[Dict[str, Any]]:
    url = f"{cfg['base_url']}/api/v1/orgs/{cfg['org_id']}/inventory"
    r = requests.get(url, headers=headers(cfg["token"]), timeout=60)
    if not r.ok:
        die(f"Failed to fetch inventory (HTTP {r.status_code}): {r.text}")
    return r.json()


def normalize_mac(mac: str) -> str:
    m = (mac or "").strip().lower().replace("-", ":")
    if ":" not in m and len(m) == 12:
        m = ":".join([m[i:i+2] for i in range(0, 12, 2)])
    return m


def read_input(filename: str, sheet_name: Optional[str]) -> pd.DataFrame:
    if not os.path.exists(filename):
        die(f"Input file '{filename}' not found")

    if filename.lower().endswith(".csv"):
        df = pd.read_csv(filename, dtype=str)
    elif filename.lower().endswith(".xlsx"):
        if sheet_name:
            df = pd.read_excel(filename, dtype=str, sheet_name=sheet_name)
        else:
            df = pd.read_excel(filename, dtype=str)
    else:
        die("Input file must be CSV or XLSX")

    return df


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # Normalize headers: strip + lowercase
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Normalize all values: fillna, cast to str, strip whitespace
    for col in df.columns:
        df[col] = df[col].fillna("").astype(str).map(lambda x: x.strip())

    return df


def validate_headers(df: pd.DataFrame) -> List[str]:
    return [c for c in REQUIRED_COLUMNS if c not in df.columns]


def validate_rows(df: pd.DataFrame, inv_map: Dict[str, Any]) -> List[RowIssue]:
    issues: List[RowIssue] = []
    for idx, row in df.iterrows():
        rownum = int(idx) + 1  # 1-based data row number
        serial = (row.get("serial number") or "").strip()

        if not serial:
            issues.append(RowIssue(row=rownum, field="serial number", value="", message="Serial Number is blank"))
            continue

        if serial not in inv_map:
            issues.append(RowIssue(row=rownum, field="serial number", value=serial, message="Serial not found in Mist org inventory"))
            continue

        mac_file = normalize_mac(row.get("mac address") or "")
        if mac_file and len(mac_file.split(":")) != 6:
            issues.append(RowIssue(row=rownum, field="mac address", value=row.get("mac address",""), message="MAC Address format looks invalid"))

    return issues


def create_validation_report(df: pd.DataFrame, issues: List[RowIssue], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"validation_report_{ts}.csv")

    rep = df.copy()
    rep["issue"] = ""
    rep["issue_field"] = ""
    rep["issue_value"] = ""

    by_row: Dict[int, List[RowIssue]] = {}
    for i in issues:
        by_row.setdefault(i.row, []).append(i)

    for idx, _ in rep.iterrows():
        rownum = int(idx) + 1
        if rownum in by_row:
            msgs, fields, values = [], [], []
            for i in by_row[rownum]:
                msgs.append(i.message)
                fields.append(i.field)
                values.append(i.value)
            rep.at[idx, "issue"] = " | ".join(msgs)
            rep.at[idx, "issue_field"] = " | ".join(fields)
            rep.at[idx, "issue_value"] = " | ".join(values)

    rep.to_csv(out_path, index=False)
    return out_path


def assign_inventory(cfg: Dict[str, str], site_id: str, mac: str) -> None:
    url = f"{cfg['base_url']}/api/v1/orgs/{cfg['org_id']}/inventory"
    payload = {"op": "assign", "site_id": site_id, "macs": [mac]}
    r = requests.put(url, headers=headers(cfg["token"]), json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Assign/update failed (HTTP {r.status_code}): {r.text}")


def main() -> None:
    setup_logging()
    logging.info("### Mist AP Site Import (CLI) started ###")

    cfg = load_config()

    filename = input("Enter CSV or XLSX filename: ").strip()
    if not filename:
        die("No filename provided")

    # Load + normalize input
    df_raw = read_input(filename, cfg.get("xlsx_sheet_name") or None)
    df = normalize_dataframe(df_raw)

    # Header validation (case-insensitive)
    missing = validate_headers(df)
    if missing:
        issues = [RowIssue(row=0, field="headers", value=", ".join(missing), message="Missing required column(s)")]
        report = create_validation_report(df.iloc[0:0], issues, os.path.dirname(os.path.abspath(filename)) or ".")
        die(f"Missing required columns: {', '.join(missing)}. Validation report written to: {report}", code=2)

    # Fetch sites + choose site
    sites = get_sites(cfg)
    site = choose_site(sites)
    site_id = site.get("id")
    site_name = site.get("name", "<unknown>")
    logging.info("Selected site: name='%s' id=%s", site_name, site_id)

    # Fetch inventory map for validation and assignment
    logging.info("Fetching inventory: %s/api/v1/orgs/%s/inventory", cfg["base_url"], cfg["org_id"])
    inventory = get_inventory(cfg)
    inv_map = {str(i.get("serial", "")).strip(): i for i in inventory if i.get("serial")}
    logging.info("Retrieved %d inventory item(s). Inventory serial map size: %d", len(inventory), len(inv_map))

    # All-or-none validation of all rows
    issues = validate_rows(df, inv_map)
    if issues:
        report = create_validation_report(df, issues, os.path.dirname(os.path.abspath(filename)) or ".")
        print("\nIMPORT BLOCKED — fix the file and re-run (all-or-none).")
        print(f"Validation report written to: {report}")
        # Print a compact summary to console
        print("\nProblems found:")
        for i in issues[:50]:
            print(f"  Row {i.row} | {i.field} | '{i.value}' | {i.message}")
        if len(issues) > 50:
            print(f"  ... and {len(issues) - 50} more. See the report for full details.")
        logging.error("Validation failed with %d issue(s). Report=%s", len(issues), report)
        sys.exit(2)

    # Add output columns
    df["assignment status"] = ""
    df["error message"] = ""

    # Execute assignments
    success = failed = 0
    for idx, row in df.iterrows():
        serial = row["serial number"].strip()
        inv = inv_map[serial]
        mac = normalize_mac(inv.get("mac") or row.get("mac address") or "")

        try:
            assign_inventory(cfg, site_id, mac)
            df.at[idx, "assignment status"] = "SUCCESS"
            success += 1
            logging.info("Row %d SUCCESS: serial=%s mac=%s", idx + 1, serial, mac)
        except Exception as e:
            df.at[idx, "assignment status"] = "FAILED"
            df.at[idx, "error message"] = str(e)
            failed += 1
            logging.error("Row %d FAILED: serial=%s error=%s", idx + 1, serial, str(e))

    # Write results file (CSV for maximum compatibility, like the Flask app)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(filename)) or "."
    base = os.path.splitext(os.path.basename(filename))[0]
    out_path = os.path.join(out_dir, f"{base}_results_{ts}.csv")
    df.to_csv(out_path, index=False)

    logging.info("Completed. SUCCESS=%d FAILED=%d Output=%s", success, failed, out_path)
    print(f"\nCompleted. SUCCESS={success} FAILED={failed}")
    print(f"Results written to: {out_path}")
    print(f"Log written to: {LOG_FILE}")


if __name__ == "__main__":
    main()

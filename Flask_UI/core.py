
import os, configparser, requests, pandas as pd
from datetime import datetime
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional

# Required fields (case-insensitive). We normalize headers to lowercase.
REQUIRED_COLUMNS = ["floor #", "wap hostname", "serial number", "mac address"]


@dataclass
class RowIssue:
    row: int               # 1-based row number as seen in spreadsheet (excluding header)
    field: str
    value: str
    message: str


class ValidationError(Exception):
    def __init__(self, issues: List[RowIssue], message: str = "Validation failed"):
        super().__init__(message)
        self.issues = issues


def load_config():
    c = configparser.ConfigParser()
    c.read("config.ini")
    return {
        "token": c["mist"]["api_token"].strip(),
        "org_id": c["mist"]["org_id"].strip(),
        "base_url": c["mist"]["base_url"].strip().rstrip("/"),
        "xlsx_sheet_name": c["mist"].get("xlsx_sheet_name", "").strip()
    }


def headers(token):
    return {"Authorization": f"Token {token}", "Content-Type": "application/json"}


def get_sites(cfg):
    r = requests.get(
        f"{cfg['base_url']}/api/v1/orgs/{cfg['org_id']}/sites",
        headers=headers(cfg["token"]),
        timeout=30
    )
    r.raise_for_status()
    return r.json()


def get_inventory(cfg):
    r = requests.get(
        f"{cfg['base_url']}/api/v1/orgs/{cfg['org_id']}/inventory",
        headers=headers(cfg["token"]),
        timeout=60
    )
    r.raise_for_status()
    return r.json()


def normalize_mac(mac: str) -> str:
    m = (mac or "").strip().lower().replace("-", ":")
    if ":" not in m and len(m) == 12:
        m = ":".join([m[i:i+2] for i in range(0,12,2)])
    return m


def _read_input(input_path: str, sheet_name: Optional[str]) -> pd.DataFrame:
    if input_path.lower().endswith(".csv"):
        return pd.read_csv(input_path, dtype=str)
    # XLSX
    if sheet_name:
        return pd.read_excel(input_path, dtype=str, sheet_name=sheet_name)
    return pd.read_excel(input_path, dtype=str)


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # Normalize headers: strip + lowercase
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Normalize all values to strings, strip whitespace
    for col in df.columns:
        df[col] = df[col].fillna("").astype(str).map(lambda x: x.strip())

    return df


def _validate_headers(df: pd.DataFrame) -> List[str]:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    return missing


def _validate_rows(df: pd.DataFrame, inv_map: Dict[str, Any]) -> List[RowIssue]:
    issues: List[RowIssue] = []

    # Row numbers: df index 0 corresponds to spreadsheet row 1 of data (header excluded)
    for idx, row in df.iterrows():
        rownum = int(idx) + 1
        serial = (row.get("serial number") or "").strip()

        if not serial:
            issues.append(RowIssue(row=rownum, field="serial number", value="", message="Serial Number is blank"))
            continue

        # Inventory check (all-or-none requires we validate existence up front)
        if serial not in inv_map:
            issues.append(RowIssue(row=rownum, field="serial number", value=serial, message="Serial not found in Mist org inventory"))
            continue

        # MAC Address column exists; value may be blank but if present validate format minimally
        mac_file = normalize_mac(row.get("mac address") or "")
        if mac_file and len(mac_file.split(":")) != 6:
            issues.append(RowIssue(row=rownum, field="mac address", value=row.get("mac address",""), message="MAC Address format looks invalid"))
            continue

    return issues


def process_file(input_path: str, site_id: str, site_name: str, results_dir: str) -> Tuple[pd.DataFrame, str, Dict[str,int]]:
    """
    All-or-none behavior:
      - Read + normalize + validate ALL rows first.
      - If any row has a problem, raise ValidationError listing all issues.
      - Only if validation passes do we execute assignments.
    """
    cfg = load_config()

    df = _read_input(input_path, cfg.get("xlsx_sheet_name") or None)
    df = _normalize_dataframe(df)

    missing = _validate_headers(df)
    if missing:
        raise ValidationError(
            issues=[RowIssue(row=0, field="headers", value=", ".join(missing), message="Missing required column(s)")],
            message="Missing required columns"
        )

    # Prepare results columns
    df["assignment status"] = ""
    df["error message"] = ""

    inventory = get_inventory(cfg)
    inv_map = {str(i.get("serial","")).strip(): i for i in inventory if i.get("serial")}

    issues = _validate_rows(df, inv_map)
    if issues:
        # Do NOT perform any assignments if any issue exists
        raise ValidationError(issues=issues, message="One or more rows are invalid")

    # Execute assignments
    ok = bad = 0
    for idx, row in df.iterrows():
        serial = row["serial number"].strip()
        inv = inv_map[serial]
        mac = normalize_mac(inv.get("mac") or row.get("mac address") or "")
        payload = {"op": "assign", "site_id": site_id, "macs": [mac]}

        try:
            r = requests.put(
                f"{cfg['base_url']}/api/v1/orgs/{cfg['org_id']}/inventory",
                headers=headers(cfg["token"]),
                json=payload,
                timeout=30
            )
            r.raise_for_status()
            df.at[idx, "assignment status"] = "SUCCESS"
            ok += 1
        except Exception as e:
            # This *should* be rare if validation passed; still capture to output
            df.at[idx, "assignment status"] = "FAILED"
            df.at[idx, "error message"] = str(e)
            bad += 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(results_dir, exist_ok=True)
    out = os.path.join(results_dir, f"results_{ts}.csv")
    df.to_csv(out, index=False)

    return df, out, {"success": ok, "failed": bad, "total": ok + bad}



def create_error_report(input_path: str, df: pd.DataFrame, issues: List[RowIssue], results_dir: str) -> str:
    """
    Create a downloadable report that highlights validation issues.
    Produces a CSV that includes the normalized input data plus Issue columns.
    """
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(results_dir, f"validation_report_{ts}.csv")

    # Add columns to df
    rep = df.copy()
    rep["issue"] = ""
    rep["issue_field"] = ""
    rep["issue_value"] = ""

    # Map issues by row for easy grouping
    by_row = {}
    for i in issues:
        if i.row <= 0:
            # header-level issues
            by_row.setdefault(0, []).append(i)
        else:
            by_row.setdefault(i.row, []).append(i)

    for idx, row in rep.iterrows():
        rownum = int(idx) + 1
        if rownum in by_row:
            msgs = []
            fields = []
            values = []
            for i in by_row[rownum]:
                msgs.append(i.message)
                fields.append(i.field)
                values.append(i.value)
            rep.at[idx, "issue"] = " | ".join(msgs)
            rep.at[idx, "issue_field"] = " | ".join(fields)
            rep.at[idx, "issue_value"] = " | ".join(values)

    # If we only have header issues, create a 1-row report
    if 0 in by_row and rep.shape[0] == 0:
        rep = pd.DataFrame([{
            "issue": " | ".join([i.message for i in by_row[0]]),
            "issue_field": " | ".join([i.field for i in by_row[0]]),
            "issue_value": " | ".join([i.value for i in by_row[0]])
        }])

    rep.to_csv(out, index=False)
    return out

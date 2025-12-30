
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
import os
import uuid

import core

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")
RESULTS_DIR = os.path.join(BASE_DIR, "data", "results")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please select a CSV or XLSX file.")
        return redirect(url_for("index"))

    if not (file.filename.lower().endswith(".csv") or file.filename.lower().endswith(".xlsx")):
        flash("Only CSV and XLSX files are supported.")
        return redirect(url_for("index"))

    job_id = uuid.uuid4().hex[:8]
    filename = f"{job_id}_{os.path.basename(file.filename)}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)

    try:
        cfg = core.load_config()
        sites = core.get_sites(cfg)
    except Exception as e:
        return render_template("error.html", message=str(e))

    # Sort sites for usability
    sites = sorted(sites, key=lambda s: (s.get("name") or "").lower())

    return render_template("select_site.html", filename=filename, sites=sites)


@app.route("/run", methods=["POST"])
def run():
    filename = (request.form.get("filename") or "").strip()
    site_id = (request.form.get("site_id") or "").strip()

    if not filename or not site_id:
        flash("Missing filename or site selection.")
        return redirect(url_for("index"))

    filepath = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(filepath):
        flash("Uploaded file not found. Please upload again.")
        return redirect(url_for("index"))

    try:
        cfg = core.load_config()
        sites = core.get_sites(cfg)
        site_name = next((s.get("name", "") for s in sites if s.get("id") == site_id), site_id)
    except Exception as e:
        return render_template("error.html", message=str(e))

    try:
        df, out_path, summary = core.process_file(filepath, site_id, site_name, RESULTS_DIR)
        return render_template(
            "results.html",
            site_name=site_name,
            summary=summary,
            out_file=os.path.basename(out_path),
            columns=list(df.columns),
            rows=df.to_dict(orient="records")
        )
    except core.ValidationError as ve:
        # Build downloadable validation report (CSV)
        report_file = None
        try:
            cfg2 = core.load_config()
            df2 = core._read_input(filepath, cfg2.get("xlsx_sheet_name") or None)
            df2 = core._normalize_dataframe(df2)
            report_path = core.create_error_report(filepath, df2, ve.issues, RESULTS_DIR)
            report_file = os.path.basename(report_path)
        except Exception:
            report_file = None

        return render_template(
            "validation_errors.html",
            message=str(ve),
            issues=[{
                "row": i.row,
                "field": i.field,
                "value": i.value,
                "message": i.message
            } for i in ve.issues],
            required_fields=["Floor #", "WAP Hostname", "Serial Number", "Mac Address"],
            report_file=report_file
        )
    except Exception as e:
        return render_template("error.html", message=str(e))


@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(RESULTS_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)

import os
import sys
import tempfile
import shutil
from flask import Flask, request, render_template_string, send_file, redirect, url_for

sys.path.insert(0, os.path.dirname(__file__))
from engine import load_and_compute, generate_html

app = Flask(__name__)
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(UPLOAD_DIR, exist_ok=True)

FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Wisdom Statement Generator</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, sans-serif; background: #f4f6f8; margin: 0; padding: 40px; }
    .card { background: #fff; border-radius: 10px; box-shadow: 0 2px 12px rgba(0,0,0,.08);
            max-width: 520px; margin: 0 auto; padding: 36px 40px; }
    h1 { margin: 0 0 6px; font-size: 1.4rem; color: #2a6b7c; }
    p.sub { margin: 0 0 28px; color: #666; font-size: .9rem; }
    label { display: block; font-size: .85rem; font-weight: 600; color: #444; margin-bottom: 5px; }
    input[type=text], input[type=file] {
      width: 100%; padding: 9px 12px; border: 1px solid #d0d7de;
      border-radius: 6px; font-size: .95rem; margin-bottom: 18px; }
    input[type=file] { padding: 7px; }
    button { background: #2a6b7c; color: #fff; border: none; border-radius: 6px;
             padding: 11px 28px; font-size: 1rem; cursor: pointer; width: 100%; }
    button:hover { background: #1e5060; }
    .error { background: #fef2f2; border: 1px solid #fca5a5; border-radius: 6px;
             color: #b91c1c; padding: 12px 16px; margin-bottom: 20px; font-size: .9rem; }
    .links { margin-top: 24px; display: flex; gap: 12px; flex-direction: column; }
    .btn-link { display: block; text-align: center; padding: 11px; border-radius: 6px;
                text-decoration: none; font-size: .95rem; font-weight: 600; }
    .btn-pdf  { background: #2a6b7c; color: #fff; }
    .btn-html { background: #f0f9fc; color: #2a6b7c; border: 1px solid #4bacc6; }
    .btn-pdf:hover { background: #1e5060; }
    .btn-html:hover { background: #d9f0f7; }
  </style>
</head>
<body>
<div class="card">
  <h1>Wisdom Statement Generator</h1>
  <p class="sub">Upload a position CSV to generate the consolidated statement.</p>

  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}

  {% if pdf_name %}
  <p style="color:#2a6b7c;font-weight:600;margin-bottom:8px;">✓ Statement generated for {{ client }}</p>
  <div class="links">
    <a class="btn-link btn-pdf"  href="/download/pdf/{{ pdf_name }}">Download PDF</a>
    <a class="btn-link btn-html" href="/preview/{{ html_name }}" target="_blank">Preview HTML</a>
  </div>
  <hr style="margin:24px 0;border:none;border-top:1px solid #e5e7eb;">
  {% endif %}

  <form method="post" enctype="multipart/form-data">
    <label>Position CSV file</label>
    <input type="file" name="csv_file" accept=".csv" required>

    <label>Client name</label>
    <input type="text" name="client" value="{{ client or '' }}" placeholder="e.g. Wisdom Group Holdings" required>

    <label>Relationship Manager</label>
    <input type="text" name="rm" value="{{ rm or '' }}" placeholder="e.g. Ethan Wang" required>

    <button type="submit">Generate Statement</button>
  </form>
</div>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template_string(FORM_HTML)

    csv_file = request.files.get("csv_file")
    client = request.form.get("client", "").strip()
    rm = request.form.get("rm", "").strip()

    if not csv_file or not csv_file.filename:
        return render_template_string(FORM_HTML, error="Please select a CSV file.", client=client, rm=rm)

    # Save uploaded CSV to a temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    csv_file.save(tmp.name)
    tmp.close()

    try:
        data = load_and_compute(tmp.name)
        html_str = generate_html(data, os.path.join(os.path.dirname(__file__), "template.html"), client, rm)

        base = os.path.splitext(csv_file.filename)[0]
        html_name = base + "_statement.html"
        pdf_name  = base + "_statement.pdf"

        html_path = os.path.join(UPLOAD_DIR, html_name)
        pdf_path  = os.path.join(UPLOAD_DIR, pdf_name)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_str)

        from weasyprint import HTML as WP_HTML
        WP_HTML(string=html_str).write_pdf(pdf_path)

    except Exception as e:
        return render_template_string(FORM_HTML, error=str(e), client=client, rm=rm)
    finally:
        os.unlink(tmp.name)

    return render_template_string(FORM_HTML, pdf_name=pdf_name, html_name=html_name, client=client, rm=rm)


@app.route("/download/pdf/<path:name>")
def download_pdf(name):
    path = os.path.join(UPLOAD_DIR, os.path.basename(name))
    return send_file(path, as_attachment=True, download_name=name)


@app.route("/preview/<path:name>")
def preview_html(name):
    path = os.path.join(UPLOAD_DIR, os.path.basename(name))
    return send_file(path, mimetype="text/html")


if __name__ == "__main__":
    app.run(debug=True, port=5050)

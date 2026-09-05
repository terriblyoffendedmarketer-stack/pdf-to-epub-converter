# web_converter.py — Local web portal for PDF to EPUB conversion
# Usage: Double-click "Start Converter.command" or run:
#   books to convert/pdf_env/bin/python3 web_converter.py
# Requires: flask, pymupdf (installed in pdf_env virtualenv)
#
# Opens a browser window at http://localhost:5151
# Upload PDFs, convert them, download EPUBs.

import os
import sys
import subprocess
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, request, jsonify, send_file, redirect, url_for

APP_DIR = Path(__file__).resolve().parent
CONVERTER = APP_DIR / "PDF to EPUB converter.py"
UPLOAD_DIR = APP_DIR / "uploads"
OUTPUT_DIR = APP_DIR / "converted"
PYTHON = APP_DIR / "books to convert" / "pdf_env" / "bin" / "python3"

app = Flask(__name__)

jobs = {}

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


def run_conversion(job_id, pdf_path):
    jobs[job_id]["status"] = "converting"
    try:
        result = subprocess.run(
            [str(PYTHON), str(CONVERTER), str(pdf_path), "-y", "-o", str(OUTPUT_DIR)],
            capture_output=True, text=True, timeout=600
        )
        epub_files = list(OUTPUT_DIR.glob("*.epub"))
        epub_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        if result.returncode == 0 and epub_files:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["epub"] = str(epub_files[0])
            jobs[job_id]["epub_name"] = epub_files[0].name
        else:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = result.stderr or result.stdout or "Conversion failed"
        jobs[job_id]["log"] = result.stdout + "\n" + result.stderr
    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "Conversion timed out (10 min limit)"
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF to EPUB Converter</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f5f5f7; color: #1d1d1f; min-height: 100vh;
    display: flex; flex-direction: column; align-items: center;
    padding: 40px 20px;
  }
  h1 { font-size: 28px; font-weight: 600; margin-bottom: 8px; }
  .subtitle { color: #86868b; font-size: 15px; margin-bottom: 32px; }

  .card {
    background: #fff; border-radius: 16px; padding: 32px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08); width: 100%; max-width: 520px;
  }

  .drop-zone {
    border: 2px dashed #d2d2d7; border-radius: 12px; padding: 48px 24px;
    text-align: center; cursor: pointer; transition: all 0.2s;
    margin-bottom: 20px;
  }
  .drop-zone:hover, .drop-zone.dragover {
    border-color: #0071e3; background: #f0f7ff;
  }
  .drop-zone .icon { font-size: 48px; margin-bottom: 12px; display: block; }
  .drop-zone .label { font-size: 16px; color: #1d1d1f; font-weight: 500; }
  .drop-zone .hint { font-size: 13px; color: #86868b; margin-top: 6px; }

  input[type="file"] { display: none; }

  .file-info {
    display: none; align-items: center; gap: 12px;
    padding: 12px 16px; background: #f5f5f7; border-radius: 10px;
    margin-bottom: 20px;
  }
  .file-info.show { display: flex; }
  .file-info .name { flex: 1; font-size: 14px; font-weight: 500; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
  .file-info .size { font-size: 13px; color: #86868b; }
  .file-info .remove { cursor: pointer; color: #86868b; font-size: 18px; }
  .file-info .remove:hover { color: #ff3b30; }

  .btn {
    width: 100%; padding: 14px; border: none; border-radius: 10px;
    font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.2s;
  }
  .btn-primary { background: #0071e3; color: #fff; }
  .btn-primary:hover { background: #0077ed; }
  .btn-primary:disabled { background: #d2d2d7; cursor: not-allowed; }
  .btn-success { background: #34c759; color: #fff; }
  .btn-success:hover { background: #30b350; }

  .progress-area { display: none; text-align: center; padding: 24px 0; }
  .progress-area.show { display: block; }

  .spinner {
    width: 40px; height: 40px; border: 3px solid #d2d2d7;
    border-top-color: #0071e3; border-radius: 50%;
    animation: spin 0.8s linear infinite; margin: 0 auto 16px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .status-text { font-size: 15px; color: #1d1d1f; }
  .status-sub { font-size: 13px; color: #86868b; margin-top: 4px; }

  .result-area { display: none; text-align: center; }
  .result-area.show { display: block; }
  .result-icon { font-size: 48px; margin-bottom: 12px; display: block; }
  .result-name { font-size: 16px; font-weight: 600; margin-bottom: 16px; }

  .error-area { display: none; text-align: center; padding: 16px 0; }
  .error-area.show { display: block; }
  .error-text { color: #ff3b30; font-size: 14px; margin-bottom: 16px; }

  .btn-reset {
    background: none; border: none; color: #0071e3; cursor: pointer;
    font-size: 14px; margin-top: 12px; padding: 8px;
  }
  .btn-reset:hover { text-decoration: underline; }

  .log-toggle {
    background: none; border: none; color: #86868b; cursor: pointer;
    font-size: 12px; margin-top: 12px; padding: 4px;
  }
  .log-area {
    display: none; margin-top: 12px; text-align: left;
    background: #1d1d1f; color: #a8f0a0; border-radius: 8px;
    padding: 12px; font-family: "SF Mono", Monaco, monospace;
    font-size: 11px; max-height: 200px; overflow-y: auto;
    white-space: pre-wrap; word-break: break-all;
  }
</style>
</head>
<body>

<h1>PDF to EPUB</h1>
<p class="subtitle">Drop a PDF, get an EPUB</p>

<div class="card">
  <div class="drop-zone" id="dropZone">
    <span class="icon">📄</span>
    <div class="label">Drop PDF here or click to browse</div>
    <div class="hint">Supports any text-based PDF</div>
  </div>
  <input type="file" id="fileInput" accept=".pdf">

  <div class="file-info" id="fileInfo">
    <span class="name" id="fileName"></span>
    <span class="size" id="fileSize"></span>
    <span class="remove" id="removeFile">&times;</span>
  </div>

  <button class="btn btn-primary" id="convertBtn" disabled>Convert to EPUB</button>

  <div class="progress-area" id="progressArea">
    <div class="spinner"></div>
    <div class="status-text">Converting...</div>
    <div class="status-sub">This usually takes 10–30 seconds</div>
  </div>

  <div class="result-area" id="resultArea">
    <span class="result-icon">✅</span>
    <div class="result-name" id="resultName"></div>
    <a id="downloadLink" href="#"><button class="btn btn-success">Download EPUB</button></a>
    <button class="btn-reset" id="convertAnother">Convert another PDF</button>
  </div>

  <div class="error-area" id="errorArea">
    <span class="result-icon">❌</span>
    <div class="error-text" id="errorText"></div>
    <button class="btn-reset" id="tryAgain">Try again</button>
  </div>

  <button class="log-toggle" id="logToggle" style="display:none">Show log</button>
  <div class="log-area" id="logArea"></div>
</div>

<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const fileInfo = document.getElementById('fileInfo');
const fileName = document.getElementById('fileName');
const fileSize = document.getElementById('fileSize');
const removeFile = document.getElementById('removeFile');
const convertBtn = document.getElementById('convertBtn');
const progressArea = document.getElementById('progressArea');
const resultArea = document.getElementById('resultArea');
const resultName = document.getElementById('resultName');
const downloadLink = document.getElementById('downloadLink');
const errorArea = document.getElementById('errorArea');
const errorText = document.getElementById('errorText');
const logToggle = document.getElementById('logToggle');
const logArea = document.getElementById('logArea');

let selectedFile = null;

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

function reset() {
  selectedFile = null;
  fileInfo.classList.remove('show');
  convertBtn.disabled = true;
  progressArea.classList.remove('show');
  resultArea.classList.remove('show');
  errorArea.classList.remove('show');
  dropZone.style.display = '';
  convertBtn.style.display = '';
  logToggle.style.display = 'none';
  logArea.style.display = 'none';
}

function selectFile(file) {
  if (!file || !file.name.toLowerCase().endsWith('.pdf')) return;
  selectedFile = file;
  fileName.textContent = file.name;
  fileSize.textContent = formatSize(file.size);
  fileInfo.classList.add('show');
  convertBtn.disabled = false;
}

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault(); dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length) selectFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) selectFile(fileInput.files[0]); });
removeFile.addEventListener('click', reset);
document.getElementById('convertAnother').addEventListener('click', reset);
document.getElementById('tryAgain').addEventListener('click', reset);

logToggle.addEventListener('click', () => {
  logArea.style.display = logArea.style.display === 'none' ? 'block' : 'none';
  logToggle.textContent = logArea.style.display === 'none' ? 'Show log' : 'Hide log';
});

convertBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  dropZone.style.display = 'none';
  fileInfo.classList.remove('show');
  convertBtn.style.display = 'none';
  progressArea.classList.add('show');

  const form = new FormData();
  form.append('pdf', selectedFile);

  try {
    const resp = await fetch('/upload', { method: 'POST', body: form });
    const data = await resp.json();
    if (!data.job_id) throw new Error(data.error || 'Upload failed');

    const jobId = data.job_id;
    while (true) {
      await new Promise(r => setTimeout(r, 1000));
      const st = await fetch('/status/' + jobId).then(r => r.json());
      if (st.status === 'done') {
        progressArea.classList.remove('show');
        resultArea.classList.add('show');
        resultName.textContent = st.epub_name;
        downloadLink.href = '/download/' + jobId;
        logToggle.style.display = '';
        logArea.textContent = st.log || '';
        break;
      }
      if (st.status === 'error') {
        progressArea.classList.remove('show');
        errorArea.classList.add('show');
        errorText.textContent = st.error || 'Conversion failed';
        logToggle.style.display = '';
        logArea.textContent = st.log || '';
        break;
      }
    }
  } catch (err) {
    progressArea.classList.remove('show');
    errorArea.classList.add('show');
    errorText.textContent = err.message;
  }
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return HTML_PAGE


@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file"}), 400

    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    pdf_path = job_dir / f.filename
    f.save(str(pdf_path))

    jobs[job_id] = {"status": "uploading", "pdf": str(pdf_path), "pdf_name": f.filename}
    t = threading.Thread(target=run_conversion, args=(job_id, pdf_path), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/download/<job_id>")
def download(job_id):
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return redirect(url_for("index"))
    return send_file(job["epub"], as_attachment=True, download_name=job["epub_name"])


if __name__ == "__main__":
    print("\n  PDF to EPUB Converter")
    print("  Open in your browser: http://localhost:5151\n")
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:5151")).start()
    app.run(host="127.0.0.1", port=5151, debug=False)

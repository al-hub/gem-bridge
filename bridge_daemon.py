#!/usr/bin/env python3
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

LOG_FILE = BASE_DIR / "result.log"
REPO_MAP = {k: Path(v) for k, v in config["repositories"].items()}
POLL_INTERVAL = config.get("poll_interval_seconds", 3)

PROCESSED_ITEMS = set()

def log_message(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass

def execute_git_task(repo_path: Path, target_path: str, content: str, commit_message: str):
    full_target_file = repo_path / target_path
    full_target_file.parent.mkdir(parents=True, exist_ok=True)

    with open(full_target_file, "w", encoding="utf-8") as f:
        f.write(content)
    log_message(f"File updated: {full_target_file}")

    subprocess.run(["git", "-C", str(repo_path), "add", target_path], check=True)
    subprocess.run(["git", "-C", str(repo_path), "commit", "-m", commit_message], check=True)
    subprocess.run(["git", "-C", str(repo_path), "push", "origin", "main"], check=True)
    log_message(f"Push successful to {repo_path.name}")

def parse_json_payload(raw_data) -> dict:
    # 1. dict 형태 (.gdoc 메타데이터)
    if isinstance(raw_data, dict):
        if "doc_id" in raw_data:
            doc_id = raw_data.get("doc_id")
            export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
            res = subprocess.run(["curl.exe", "-sL", export_url], capture_output=True, text=True, errors="ignore")
            # 로그인 페이지(HTML)가 아닌 실제 텍스트인지 검증
            if res.stdout.strip() and not res.stdout.strip().startswith("<!DOCTYPE"):
                raw_data = res.stdout.strip()
            else:
                raise ValueError(f"Google Docs export requires public sharing: doc_id={doc_id}")
        else:
            return raw_data

    # 2. 문자열 형태
    text = str(raw_data).strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    if '"doc_id"' in text and '"url"' in text:
        meta = json.loads(text)
        doc_id = meta.get("doc_id")
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
        res = subprocess.run(["curl.exe", "-sL", export_url], capture_output=True, text=True, errors="ignore")
        if res.stdout.strip() and not res.stdout.strip().startswith("<!DOCTYPE"):
            text = res.stdout.strip()
        else:
            raise ValueError(f"Google Docs export requires public sharing: doc_id={doc_id}")

    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1:
        text = text[start_idx:end_idx + 1]

    return json.loads(text)

def check_and_process_windows_drive():
    # .json 파일을 우선 감시
    ps_script = """
    $targets = @("G:\\내 드라이브\\GeminiBridge\\*.json", "G:\\내 드라이브\\task*.json", "G:\\내 드라이브\\GeminiBridge\\*.gdoc", "G:\\내 드라이브\\task*.gdoc")
    $files = Get-ChildItem -Path $targets -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        $content = Get-Content -LiteralPath $f.FullName -Raw -ErrorAction SilentlyContinue
        [PSCustomObject]@{
            FullName = $f.FullName
            Name = $f.Name
            Content = $content
        } | ConvertTo-Json -Compress
    }
    """
    
    cmd = ["powershell.exe", "-NoProfile", "-Command", f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; {ps_script}"]
    res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip().startswith("{")]
    for line in lines:
        try:
            item = json.loads(line)
            file_name = item.get("Name")
            full_path = item.get("FullName")
            content = item.get("Content")

            if not file_name or file_name in PROCESSED_ITEMS:
                continue

            PROCESSED_ITEMS.add(file_name)
            log_message(f"Processing candidate: {file_name}")

            data = parse_json_payload(content)
            repo_key = data.get("repo")
            target_path = data.get("target_path")
            file_content = data.get("content")
            commit_message = data.get("commit_message", f"update: {target_path} via Gemini Bridge")

            if repo_key not in REPO_MAP:
                raise ValueError(f"Unknown repo '{repo_key}'")

            execute_git_task(REPO_MAP[repo_key], target_path, file_content, commit_message)

            del_cmd = ["powershell.exe", "-NoProfile", "-Command", f"Remove-Item -LiteralPath '{full_path}' -Force"]
            subprocess.run(del_cmd, capture_output=True)
            log_message(f"Task finished and removed: {file_name}")

        except Exception as e:
            log_message(f"[ERROR] Failed processing {file_name}: {e}")

def main():
    log_message("=== Gemini Bridge Daemon (v6) Started ===")
    while True:
        try:
            check_and_process_windows_drive()
        except Exception as e:
            log_message(f"Bridge loop error: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

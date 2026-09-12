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

BRIDGE_DIR = Path(config["bridge_dir"])
DRIVE_ROOT = BRIDGE_DIR.parent  # /mnt/g/내 드라이브
PROCESSED_DIR = BRIDGE_DIR / "processed"
LOG_FILE = BRIDGE_DIR / "result.log"
REPO_MAP = {k: Path(v) for k, v in config["repositories"].items()}
POLL_INTERVAL = config.get("poll_interval_seconds", 3)

PROCESSED_NAMES = set()

def log_message(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted)
    try:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass

def read_file_content(file_path: Path) -> str:
    """WSL I/O 에러 방지를 위해 Windows powershell.exe를 통해 안전하게 파일 본문 읽기"""
    # Windows 경로로 변환 (예: /mnt/g/내 드라이브/... -> G:\내 드라이브\...)
    wsl_str = str(file_path.resolve())
    if wsl_str.startswith("/mnt/g/"):
        win_path = "G:\\" + wsl_str[len("/mnt/g/"):].replace("/", "\\")
    else:
        win_path = wsl_str

    cmd = ["powershell.exe", "-NoProfile", "-Command", f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-Content -LiteralPath '{win_path}' -Raw"]
    res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()

    # 일반 읽기 폴백
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()

def remove_drive_file(file_path: Path):
    """Google Drive 가상 파일 삭제/이동 처리"""
    wsl_str = str(file_path.resolve())
    if wsl_str.startswith("/mnt/g/"):
        win_path = "G:\\" + wsl_str[len("/mnt/g/"):].replace("/", "\\")
        cmd = ["powershell.exe", "-NoProfile", "-Command", f"Remove-Item -LiteralPath '{win_path}' -Force"]
        subprocess.run(cmd, capture_output=True)
    else:
        file_path.unlink(missing_ok=True)

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

def parse_json_payload(raw_text: str) -> dict:
    text = raw_text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # Google Docs 메타데이터인 경우 url/doc_id 파싱 확인
    if '"url":' in text and '"doc_id":' in text:
        try:
            meta = json.loads(text)
            doc_id = meta.get("doc_id")
            if doc_id:
                # 윈도우 curl로 텍스트 내보내기 시도
                export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
                res = subprocess.run(["curl.exe", "-sL", export_url], capture_output=True, text=True, errors="ignore")
                if res.stdout.strip():
                    text = res.stdout.strip()
        except Exception:
            pass

    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1:
        text = text[start_idx:end_idx + 1]

    return json.loads(text)

def process_task_file(file_path: Path):
    if file_path.name in PROCESSED_NAMES:
        return

    log_message(f"Discovered task candidate: {file_path.name}")
    PROCESSED_NAMES.add(file_path.name)
    time.sleep(1)

    try:
        raw_text = read_file_content(file_path)
        data = parse_json_payload(raw_text)

        repo_key = data.get("repo")
        target_path = data.get("target_path")
        content = data.get("content")
        commit_message = data.get("commit_message", f"update: {target_path} via Gemini Bridge")

        if not repo_key or repo_key not in REPO_MAP:
            raise ValueError(f"Target repo '{repo_key}' invalid. Configured: {list(REPO_MAP.keys())}")

        execute_git_task(REPO_MAP[repo_key], target_path, content, commit_message)

        # 처리 완료 후 원본 태스크 삭제
        remove_drive_file(file_path)
        log_message(f"Task successfully completed and file cleaned up: {file_path.name}")

    except Exception as e:
        log_message(f"[ERROR] Processing {file_path.name}: {str(e)}")

def scan_for_tasks():
    candidates = []
    # 1. GeminiBridge 폴더
    if BRIDGE_DIR.exists():
        for p in BRIDGE_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in [".json", ".gdoc", ".txt"]:
                candidates.append(p)

    # 2. 내 드라이브 루트에서 task* 또는 gem_* 시작 파일
    if DRIVE_ROOT.exists():
        for p in DRIVE_ROOT.iterdir():
            if p.is_file() and (p.name.startswith("task") or p.name.startswith("gem_")):
                if p.suffix.lower() in [".json", ".gdoc", ".txt"]:
                    candidates.append(p)

    for task_file in candidates:
        process_task_file(task_file)

def main():
    log_message("=== Gemini Bridge Daemon (v3: Windows I/O Fallback) Started ===")
    while True:
        try:
            scan_for_tasks()
        except Exception as e:
            log_message(f"Watcher loop error: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

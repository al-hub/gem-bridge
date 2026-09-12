#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

# 기본 감시 대상: GeminiBridge 폴더 및 내 드라이브 루트
BRIDGE_DIR = Path(config["bridge_dir"])
DRIVE_ROOT = BRIDGE_DIR.parent  # /mnt/g/내 드라이브
PROCESSED_DIR = BRIDGE_DIR / "processed"
LOG_FILE = BRIDGE_DIR / "result.log"
REPO_MAP = {k: Path(v) for k, v in config["repositories"].items()}
POLL_INTERVAL = config.get("poll_interval_seconds", 3)

def log_message(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted)
    try:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception as e:
        print(f"Log write failed: {e}")

def extract_text_from_file(file_path: Path) -> str:
    """일반 파일(.json/.txt) 또는 Google Docs 바로가기(.gdoc)에서 텍스트 추출"""
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_content = f.read().strip()

    # .gdoc 파일인 경우 doc_id 파싱 후 텍스트 다운로드 시도
    if file_path.suffix.lower() == ".gdoc" or '"doc_id"' in raw_content:
        try:
            doc_meta = json.loads(raw_content)
            doc_id = doc_meta.get("doc_id")
            if doc_id:
                export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
                req = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp:
                    return resp.read().decode("utf-8").strip()
        except Exception as e:
            log_message(f"Public export failed ({e}), fallback to local raw text")

    return raw_content

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
    """마크다운 코드블록이나 불필요한 줄바꿈 제거 후 JSON 객체 추출"""
    text = raw_text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    
    # 텍스트 내에서 최초 { 부터 마지막 } 까지 슬라이싱 (앞뒤 잡음 제거)
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1:
        text = text[start_idx:end_idx + 1]

    return json.loads(text)

def process_task_file(file_path: Path):
    log_message(f"Discovered task candidate: {file_path.name}")
    time.sleep(1)  # 동기화 완료 버퍼

    try:
        raw_text = extract_text_from_file(file_path)
        data = parse_json_payload(raw_text)

        repo_key = data.get("repo")
        target_path = data.get("target_path")
        content = data.get("content")
        commit_message = data.get("commit_message", f"update: {target_path} via Gemini Bridge")

        if not repo_key or repo_key not in REPO_MAP:
            raise ValueError(f"Target repo '{repo_key}' invalid. Configured: {list(REPO_MAP.keys())}")

        execute_git_task(REPO_MAP[repo_key], target_path, content, commit_message)

        # 처리 완료 파일 아카이빙
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        dest = PROCESSED_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file_path.name}"
        file_path.rename(dest)
        log_message(f"Task finished and moved to: {dest.name}")

    except Exception as e:
        log_message(f"[ERROR] Processing {file_path.name}: {str(e)}")
        error_path = file_path.with_suffix(file_path.suffix + ".error")
        file_path.rename(error_path)

def scan_for_tasks():
    # 1. GeminiBridge 폴더 내 모든 .json / .gdoc
    candidates = []
    if BRIDGE_DIR.exists():
        for p in BRIDGE_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in [".json", ".gdoc", ".txt"] and not p.name.endswith(".error"):
                candidates.append(p)

    # 2. 내 드라이브 루트에서 'task' 또는 'gem_'으로 시작하는 파일 탐색
    if DRIVE_ROOT.exists():
        for p in DRIVE_ROOT.iterdir():
            if p.is_file() and (p.name.startswith("task") or p.name.startswith("gem_")):
                if p.suffix.lower() in [".json", ".gdoc", ".txt"] and not p.name.endswith(".error"):
                    candidates.append(p)

    for task_file in candidates:
        process_task_file(task_file)

def main():
    log_message("=== Gemini Bridge Daemon (v2: Multi-format & Root Watch) Started ===")
    while True:
        try:
            scan_for_tasks()
        except Exception as e:
            log_message(f"Watcher loop error: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

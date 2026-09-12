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
PROCESSED_DIR = BRIDGE_DIR / "processed"
LOG_FILE = BRIDGE_DIR / "result.log"
REPO_MAP = {k: Path(v) for k, v in config["repositories"].items()}
POLL_INTERVAL = config.get("poll_interval_seconds", 3)

def log_message(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception as e:
        print(f"Log write failed: {e}")

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

def process_task_file(file_path: Path):
    log_message(f"Discovered task: {file_path.name}")
    time.sleep(1)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_text = f.read().strip()

        if raw_text.startswith("```json"):
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        data = json.loads(raw_text)
        repo_key = data.get("repo")
        target_path = data.get("target_path")
        content = data.get("content")
        commit_message = data.get("commit_message", f"update: {target_path} via Gemini Bridge")

        if repo_key not in REPO_MAP:
            raise ValueError(f"Target repository '{repo_key}' is not defined in config.json")

        execute_git_task(REPO_MAP[repo_key], target_path, content, commit_message)

        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        dest = PROCESSED_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file_path.name}"
        file_path.rename(dest)
        log_message(f"Task finished and moved to: {dest.name}")

    except Exception as e:
        log_message(f"[ERROR] {file_path.name}: {str(e)}")
        file_path.rename(file_path.with_suffix(".error"))

def main():
    log_message("=== Gemini Bridge Daemon Started ===")
    while True:
        try:
            if BRIDGE_DIR.exists():
                for task_file in BRIDGE_DIR.glob("*.json"):
                    process_task_file(task_file)
        except Exception as e:
            log_message(f"Watcher loop error: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

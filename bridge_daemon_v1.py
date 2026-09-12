import os
import sys
import json
import time
import subprocess
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google import genai

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TOKEN_PATH = BASE_DIR / "token.json"
LOG_PATH = BASE_DIR / "result.log"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

def log_message(msg: str):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_drive_service():
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        creds_data = json.load(f)
    creds = Credentials.from_authorized_user_info(creds_data)
    return build("drive", "v3", credentials=creds)

def parse_with_llm(raw_text: str, api_key: str, available_repos: list) -> dict:
    client = genai.Client(api_key=api_key)
    prompt = f"""
    당신은 Git 자동화 브리지 에이전트입니다. 사용자가 보낸 구글 문서 본문을 분석하여
    Git 저장소에 반영할 파일 명세(JSON)를 작성하세요.
    문서 본문에 정리된 상세 내용(가이드, 트러블슈팅, 아키텍처 등)을 온전히 마크다운 포맷으로 content 필드에 빠짐없이 담으세요.

    [사용 가능한 로컬 저장소 목록]
    {available_repos}

    [입력 문서 원문]
    {raw_text}

    반드시 아래 JSON 스키마를 엄격히 준수하여 순수 JSON만 반환하세요:
    {{
      "repo": "gem-bridge",
      "target_path": "docs/ARCHITECTURE.md",
      "content": "상세한 마크다운 문서 전문",
      "commit_message": "docs: enrich ARCHITECTURE.md with full setup guide and troubleshooting"
    }}
    """
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
        config={"response_mime_type": "application/json"}
    )
    text = response.text.strip()
    return json.loads(text)

def parse_json_payload(raw_text: str) -> dict:
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(cleaned)
    except Exception:
        api_key = CONFIG.get("gemini_api_key")
        if api_key:
            return parse_with_llm(raw_text, api_key, list(CONFIG.get("repositories", {}).keys()))
        raise

def execute_git_task(repo_path: Path, target_path: str, content: str, commit_message: str):
    full_path = repo_path / target_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    log_message(f"파일 작성 완료: {full_path}")

    subprocess.run(["git", "add", str(full_path)], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", commit_message], cwd=repo_path, check=True)
    subprocess.run(["git", "push"], cwd=repo_path, check=True)
    log_message(f"Git 커밋 및 푸시 완료: {commit_message}")

def process_drive_tasks(service):
    query = "mimeType = 'application/vnd.google-apps.document' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)", pageSize=15).execute()
    raw_files = results.get("files", [])
    keywords = ["!", "깃", "task", "작업", "gem-bridge"]
    files = [f for f in raw_files if any(k in f.get("name", "") for k in keywords)]

    if not files:
        return

    for f in files:
        file_id = f["id"]
        file_name = f["name"]
        log_message(f"대상 문서 감지: {file_name} ({file_id})")

        try:
            raw_text = service.files().export_media(
                fileId=file_id,
                mimeType="text/plain"
            ).execute().decode("utf-8")

            payload = parse_json_payload(raw_text)
            repo_name = payload.get("repo")
            target_path = payload.get("target_path")
            content = payload.get("content")
            commit_message = payload.get("commit_message", f"update from drive task: {file_name}")

            repo_mapping = CONFIG.get("repositories", {})
            if repo_name not in repo_mapping:
                # 기본 저장소 폴백
                if "gem-bridge" in repo_mapping:
                    repo_dir = Path(repo_mapping["gem-bridge"])
                else:
                    repo_dir = BASE_DIR
            else:
                repo_dir = Path(repo_mapping[repo_name])

            execute_git_task(repo_dir, target_path, content, commit_message)

            # 성공 시 구글 문서를 휴지통으로 이동
            service.files().update(fileId=file_id, body={"trashed": True}).execute()
            log_message(f"문서 처리 완료 및 휴지통 이동: {file_name}")

        except Exception as e:
            log_message(f"처리 중 오류 발생 ({file_name}): {e}")

def main():
    log_message("Gemini Mobile to Git Bridge Daemon 시작")
    service = get_drive_service()
    while True:
        try:
            process_drive_tasks(service)
        except Exception as e:
            log_message(f"데몬 루프 에러: {e}")
        time.sleep(5)

if __name__ == "__main__":
    main()

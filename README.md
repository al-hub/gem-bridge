# gem-bridge

Google Gemini Mobile App과 로컬 리포지토리를 연결하는 Google Drive 기반 자동화 브리지 데몬.

## 구조
- `bridge_daemon.py`: Google Drive 폴더(`GeminiBridge`)의 태스크 JSON을 감시하여 Git push 실행
- `config.json`: 감시 경로 및 관리 대상 리포지토리 매핑 설정

## 실행 방법
```bash
python3 bridge_daemon.py 

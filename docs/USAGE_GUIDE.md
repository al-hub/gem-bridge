# gem-bridge 모바일 및 일상 사용 가이드 (Usage Guide)

스마트폰의 **Google Gemini 모바일 앱** 또는 **Google Docs**를 통해 PC의 로컬/원격 Git 저장소를 원격으로 조작하는 일상 사용법 안내입니다.

---

## 1. 기본 동작 메커니즘

```
[스마트폰] Gemini 모바일 앱 또는 Google Docs 작성
   │  (문서 제목에 '!' 또는 키워드 포함)
   ▼
[Google Drive] 작업 문서 동기화
   │
   ▼
[PC 데몬] daemon_v2가 문서 감지 ➔ 작업 분류 (READ/WRITE/EXEC) ➔ 저장소 처리
   │
   ├─► READ 작업   ──► Google Drive에 '[보고서] ...' 신규 문서 생성
   ├─► WRITE 작업  ──► Git commit & push 수행 + '[완료] ...' 결과 문서 생성
   ├─► EXEC 작업   ──► 명령어 실행 후 '[실행결과] ...' 독스 생성
   └─► 오류 발생 시 ──► '[오류] ...' 독스 생성 (데몬은 중단 없이 유지)
   │
   ▼
[Google Drive] 처리가 끝난 원본 문서는 '휴지통'으로 자동 이동
```

---

## 2. 명령어 접두어 및 활용 예시

문서의 **제목** 또는 **첫 줄**에 아래 접두어를 입력하면 원하는 작업 성격을 명확히 지정할 수 있습니다.

### (1) `!분석` (READ 전용 - 소스코드 조회 및 심층 보고서 요청)
- **특징**: **절대 Git Push나 파일 수정을 하지 않습니다.** 저장소 전체 컨텍스트를 수집하여 전문가 수준의 마크다운 분석 보고서를 Google Docs로 회신합니다.
- **제목 예시**:
  - `!분석 gem-bridge의 intent_analyzer 모듈 역할 및 스키마 강제 방식 분석`
  - `!분석 my-app 최근 로그인 에러 관련 코드 흐름 추적해줘`
- **본문 예시**:
  ```text
  core/intent_analyzer.py와 core/repo_manager.py의 인터페이스가 Michael Feathers의 Seam 원칙을 잘 따르고 있는지 분석하고, 개선 포인트를 제안해줘.
  ```
- **회신 결과**: Google Drive에 `[보고서] ...` 제목의 정형 문서가 수 초 내로 자동 생성됩니다.

---

### (2) `!작업` (WRITE 전용 - 소스코드 생성/수정 및 Git Commit & Push)
- **특징**: 변경할 파일과 반영 내용을 검증한 후, 변경 전후 Unified Diff를 생성하고 자동으로 `git add`, `git commit`, `git push`를 수행합니다.
- **보호 가드레일**:
  - `README.md`, `ARCHITECTURE.md`, `credentials.json`, `token.json`, `.env` 등의 보호 파일은 덮어쓰기가 원천 차단됩니다.
  - 구체적인 파일 경로(`target_path`)나 코드 내용(`content`)이 누락되어 모호한 지시는 **자동으로 READ(보고서)로 다운그레이드**되어 코드가 손상되지 않습니다.
- **제목 예시**:
  - `!작업 gem-bridge docs/NEW_FEATURE.md 가이드 문서 신규 추가`
- **본문 예시**:
  ```markdown
  파일명: docs/NEW_FEATURE.md
  
  # 신규 기능 가이드
  여기에 작성할 상세 마크다운 내용을 온전히 적어주세요.
  ```
- **회신 결과**:
  - 저장소에 커밋/푸시 완료.
  - Google Drive에 커밋 해시와 Diff가 포함된 `[완료] ...` 문서 생성.

---

### (3) `!실행` (EXEC 전용 - 테스트 및 빌드 커맨드 실행)
- **특징**: 지정된 리포지토리 디렉토리에서 안전하게 터미널 명령을 실행하고 콘솔 출력(stdout, stderr)을 회신받습니다.
- **보호 가드레일**: `rm -rf /`, `mkfs` 등 시스템 파괴 명령은 실행이 거부됩니다.
- **제목 예시**:
  - `!실행 gem-bridge python3 -m unittest discover -s tests -v`
  - `!실행 my-app npm test`
- **회신 결과**: Google Drive에 `[실행결과] ...` 문서로 전체 터미널 출력이 회신됩니다.

---

### (4) 정형 JSON 직접 입력 (고급 개발자/스크립트용)
문서 본문에 정형 JSON을 직접 작성하면 LLM 추론 없이 즉시 지정된 작업을 수행합니다:

```json
{
  "repo": "gem-bridge",
  "target_path": "docs/STATUS.md",
  "content": "# 현재 상태\n모든 파이프라인 정상 가동 중",
  "commit_message": "docs: update status"
}
```

---

## 3. 스마트폰 Google Gemini 앱을 통한 워크플로우

1. **Gemini 모바일 앱 열기**:
   - 프롬프트 예시:
     > "내가 관리하는 gem-bridge 리포지토리의 daemon_v2.py의 에러 처리 방식을 분석하고 싶어. 구글 문서로 `!분석 gem-bridge 데몬 에러 핸들링 구조`라는 제목으로 문서를 만들어줘."
2. **구글 문서 자동 생성**:
   - Gemini가 구글 드라이브에 문서를 생성합니다.
3. **PC 데몬 자동 감지**:
   - PC의 `gem-bridge` 데몬이 수 초 내 문서를 감지하고 처리합니다.
4. **결과 확인**:
   - 스마트폰 구글 드라이브 앱 알림을 통해 `[보고서] ...` 문서가 도착한 것을 즉시 확인하고 읽을 수 있습니다.

---

## 4. 문제 발생 시 확인법

작업 수행 도중 오류가 발생하면:
1. Google Drive에 **`[오류] {원래문서제목}`** 문서가 자동 생성됩니다.
2. 해당 문서를 열면 **상세 파이썬 에러 트레이스백과 조치 가이드**가 기재되어 있습니다.
3. PC 데몬은 예외로 인해 꺼지지 않고 계속 정상 실행 상태를 유지합니다.

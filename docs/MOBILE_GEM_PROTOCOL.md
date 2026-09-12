# gem-bridge 모바일 Gem 결정론적 프로토콜 (Mobile Gem Deterministic Protocol)

> **문서 버전**: v2.1.8  
> **역할**: Mobile LLM Protocol & DevOps Architect  
> **목적**: 스마트폰 Google Gemini 모바일 앱에서 사용자가 수동으로 텍스트를 복사-붙여넣기하거나 앱을 전환하는 **'클립보드 심부름(Human Proxy)'을 원천 근절**하고, **Gemini 내장 `@Google Tasks` 무터치(0-Tap)** 및 **In-Chat 0-Click 피드백 루프**를 통해 **채팅창을 벗어나지 않는 완벽한 모바일 All-in-One 개발 환경**을 구축함.

---

## 1. 지능형 3-Tier 라우팅 매트릭스 (Core Architecture)

사용자의 질의 의도(Intent)에 따라 시스템은 3가지 경로로 명확히 분기하여 불필요한 앱 전환이나 수동 작업을 100% 차단합니다:

```
[스마트폰 Google Gemini 모바일 앱]
       │
       ▼
[의도 분석 (Intent Classification)]
       │
       ├─► [Tier 1: 인챗 즉시 완결 (0-Tap In-Chat Direct)]
       │     • 대상: 공개 GitHub 저장소/Pages 분석, 웹 상태 점검, 코드 리뷰, 설계/문법 질문
       │     • 동작: Gemini가 내장 웹 브라우징/지식으로 채팅창에서 즉시 100% 완결.
       │             (명령어 블록 생성이나 구글 문서 이동 심부름 일절 금지!)
       │
       ├─► [Tier 2: 로컬 실행 위임 (0-Tap Google Tasks & Zero-Copy)]
       │     • 대상: 로컬 파일 실제 수정/Git Push, 로컬 터미널 실행, 로컬 테스트(pytest)
       │     • 동작: 코드 변경 전략/Diff 설명 후, 사용자가 Gemini에서
       │             "@Google Tasks <작업내용> 등록해줘" (또는 "할 일에 ... 등록해줘")라고
       │             말만 하면 로컬 PC 데몬(v2.1.8)이 0-Tap으로 감지하여 Git 푸시까지 완료!
       │
       └─► [Tier 3: 인챗 결과 피드백 (0-Click Feedback)]
             • 대상: "방금 작업 결과 알려줘", "어떻게 됐어?", "커밋 확인해줘"
             • 동작: Gemini가 @Google Drive 확장을 통해 [최신결과] CONSOLE 최상단을 자동 조회하여
                     채팅창에 커밋 해시와 결과 브리핑을 직접 출력 (외부 앱 이동 0회).
```

---

## 2. 커스텀 Gem 전용 최신 시스템 프롬프트 (Production-Ready)

아래 프롬프트 전문을 Google Gemini 커스텀 Gem의 **Instructions (지침)** 란에 복사하여 설정합니다.

```markdown
# Role: gem-bridge Intelligent Mobile Copilot

당신은 개발자의 스마트폰 모바일 환경과 로컬 PC(WSL gem-bridge)를 유기적으로 연결하는 전담 코파일럿입니다.
사용자에게 텍스트를 복사해서 다른 앱에 붙여넣으라고 심부름시키는 것을 엄격히 금지합니다.

---

## [3대 핵심 행동 원칙]

1. [자체 완결 우선 원칙 (Self-Sufficiency First)]
   - 공개 GitHub 저장소(github.com/...), 배포된 Pages 웹사이트(*.github.io), 기술 문서, 에러 로그 해석, 아키텍처 질문 등은 당신의 지식과 내장 웹 브라우징을 활용하여 **채팅창에서 즉시 100% 답변을 완결**하십시오.
   - 이때는 `!분석` 명령어 블록이나 구글 문서 링크를 절대로 출력하지 마십시오.

2. [로컬 작업 선별 위임 (0-Tap @Google Tasks)]
   - 사용자가 **"로컬 파일 직접 수정 및 Git Commit & Push"**, **"로컬 빌드 및 단위 테스트 실행"**, **"로컬 PC 전용 터미널 명령"**을 명시적으로 요구할 때만 로컬 PC 데몬(gem-bridge)을 연계하십시오.
   - 변경 전략을 채팅으로 설명한 후, 사용자가 복사-붙여넣기나 앱 전환 없이 **"@Google Tasks <작업 내용> 등록해줘"**라고 말 한마디만 하면 로컬 PC 데몬(v2.1.8)이 0-Tap으로 자동 감지하여 GitHub 푸시까지 완료하도록 친절히 안내하십시오.

3. [인챗 결과 피드백 (In-Chat Feedback)]
   - 사용자가 "결과 알려줘", "어떻게 됐어?"라고 물으면, 구글 드라이브에서 `[최신결과] CONSOLE` 문서를 읽어 **채팅창에 커밋 해시와 완료 결과를 직접 브리핑**하십시오.

---

## [상황별 응답 프로토콜]

### 상황 A. 공개 저장소 / Pages / 웹 분석 / 일반 코드 질의 (Tier 1)
(예: `https://github.com/... pages 현황 분석해줘`, `이 코드 리뷰해줘`, `아키텍처 설명해줘`)
- **실행**: 웹 검색/브라우징을 통해 저장소나 배포 사이트를 실시간으로 확인하고, **채팅창에 즉시 완성형 마크다운 분석 보고서를 출력**합니다.
- **비공개(Private) 저장소 또는 웹 접근 실패 시 예외 처리**:
  만약 해당 URL이 비공개(Private) 저장소이거나 404/접근 불가인 경우, 절대 거절 멘트를 하지 말고 로컬 PC 데몬(gem-bridge)이 로컬 GitHub 인증을 통해 안전하게 분석할 수 있도록 아래 0-Tap 안내를 즉시 출력하십시오:
  "🔒 해당 저장소는 개인 비공개(Private) 저장소이거나 웹에서 직접 접근할 수 없습니다. 로컬 PC 데몬이 귀하의 GitHub SSH/CLI 인증을 통해 안전하게 분석할 수 있도록 등록해 드릴게요!
  👉 `@Google Tasks <저장소명 또는 URL> 분석해줘 등록해줘`"

### 상황 B. 로컬 코드 수정 및 Git Push 요청 (Tier 2)
(예: `gem-bridge docs/guide.md 수정하고 푸시해줘`, `버그 수정해줘`, `pytest 돌려줘`)
1. 변경 사항 및 코드 Diff를 채팅창에서 먼저 명확히 설명합니다.
2. 0-Tap 음성/텍스트 실행 가이드를 제공합니다:
   ⚡ **[완전 무터치 0-Tap 실행 방법]**  
   아래와 같이 말씀하시면, Google Tasks를 통해 로컬 PC 데몬(v2.1.8)이 1초 내에 감지하여 Git 푸시까지 자동으로 완료합니다!
   
   👉 `@Google Tasks <저장소명> <수정 내용> 등록해줘`
   (또는 `할 일에 <저장소명> <수정 내용> 등록해줘`)

   📱 (보조 수단) [CONSOLE 직접 열기](https://docs.google.com/document/d/1SE7DPvOhUpnGgUJ84KBqmyKaFY2a287SwmWSenTA3tk/edit)

### 상황 C. "결과 알려줘" / 상태 확인 요청 (Tier 3)
- Google Workspace 확장을 통해 `[최신결과] CONSOLE` 최상단의 결과를 읽어 채팅창에 즉시 출력합니다:
   ✅ [작업 완료 브리핑]
   - 커밋 해시: <문서에서 읽은 커밋 해시>
   - 변경 요약: <문서에서 읽은 변경 파일 및 요약>
```

---

## 3. 스마트폰 실전 사용 가이드 (Mobile Walkthrough)

### 3.1 코드 수정 & 푸시 작업 시 (0-Tap Google Tasks)
1. 스마트폰 Gemini 앱에서 지시:  
   > *"@Google Tasks gem-bridge docs/guide.md에 모바일 사용법 추가하고 푸시해줘 등록해줘"*
2. **[사용자 동작]**: **터치 0회 (말 한마디로 등록 완료)**
3. **[백그라운드 자동 처리]**:
   - 로컬 PC 데몬(v2.1.8)이 Google Tasks를 실시간 감지하여 코드를 수정하고 원격 GitHub `origin/main`으로 푸시합니다.
   - 작업 완료 후 해당 할 일은 자동으로 완료(Checked) 처리되며 커밋 해시가 메모에 기록됩니다.

### 3.2 결과 확인 시 (In-Chat 0-Click)
1. 스마트폰 Gemini 앱에서 질문:  
   > *"방금 작업 어떻게 됐어?"* 또는 *"결과 알려줘"*
2. Gemini가 드라이브의 `[최신결과] CONSOLE` 최상단을 읽어 채팅창에 즉시 커밋 해시와 완료 요약을 출력합니다.

---

## 4. [선택 사항] Google Apps Script (GAS) 1-Click 웹훅 연동

만약 구글 문서 내보내기 대신 **Gemini 채팅창의 파란색 링크 1회 탭**으로 실행하고 싶다면, 개인 구글 드라이브에 아래 Apps Script를 웹 앱으로 배포할 수 있습니다.

```javascript
/**
 * gem-bridge 1-Click Webhook Trigger (Code.gs)
 * 사용자가 링크를 누르면 CONSOLE의 >>> INPUT >>>에 명령어를 자동 주입합니다.
 */
function doGet(e) {
  var cmd = e.parameter.cmd;
  if (!cmd) return HtmlService.createHtmlOutput("<h3>⚠️ 명령어가 없습니다.</h3>");

  var files = DriveApp.getFilesByName("[최신결과] CONSOLE");
  if (!files.hasNext()) return HtmlService.createHtmlOutput("<h3>⚠️ CONSOLE 문서를 찾을 수 없습니다.</h3>");

  var doc = DocumentApp.openById(files.next().getId());
  var body = doc.getBody();
  var newBlock = ">>> INPUT >>>\n" + cmd.trim() + "\n<<< END <<<";
  body.replaceText("(?s)>>>\\s*INPUT\\s*>>>.*?<<<\\s*END\\s*<<<", newBlock);
  doc.saveAndClose();

  return HtmlService.createHtmlOutput(`
    <body style="font-family:sans-serif; text-align:center; padding:40px; background:#0f172a; color:white;">
      <h2>🚀 로컬 PC 데몬에 전송 완료!</h2>
      <p style="color:#94a3b8;">1초 내에 작업이 시작됩니다. Gemini 앱으로 돌아가세요.</p>
      <script>setTimeout(function(){ window.close(); }, 1200);</script>
    </body>
  `);
}
```

---

## 5. 회귀 방지 검증 시나리오 (Test Matrix)

| 시나리오 | 사용자 입력 | 기대 동작 (PASS 기준) | 금지 동작 (FAIL 기준) |
| :--- | :--- | :--- | :--- |
| **S1. GitHub Pages 분석** | `https://github.com/al-hub/gem-bridge 의 pages 현황 분석해줘` | 웹 브라우징으로 Pages 배포 상태, HTTP 응답, 서빙 문서를 **채팅창에 즉시 분석 보고서로 출력** | 명령어 블록 생성 및 구글 문서 앱으로 이동하라고 심부름 시키기 |
| **S2. 로컬 코드 수정 및 푸시** | `gem-bridge README에 최신 파이프라인 추가하고 푸시해줘` | 변경 설명 + 트리거 블록 + **[공유] ➔ [Google 문서로 내보내기] 원터치 가이드** 제공 | 텍스트 복사해서 구글 문서에 붙여넣으라고 요구하기 |
| **S3. 결과 질의** | `방금 한 거 어떻게 됐어?` | `[최신결과] CONSOLE` 상단을 읽어 **채팅창에서 결과 즉시 브리핑** | 구글 문서 앱을 열어보라고 안내하기 |
| **S4. 내보내기 문서 자동 청소** | 사용자가 `[Google 문서로 내보내기]` 실행 | `daemon_v2.py`가 감지하여 작업 완료 후 원본 `Gemini - *` 문서를 휴지통으로 이동 | 임시 문서가 드라이브에 방치되어 중복 실행 발생 |


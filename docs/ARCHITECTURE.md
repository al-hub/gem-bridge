# gem-bridge Architecture: `core/intent_analyzer.py` 상세 분석

## 1. 모듈 개요 및 역할
`core/intent_analyzer.py` 모듈은 **gem-bridge** 시스템의 핵심 컴포넌트로, 입력된 구글 문서 본문이나 자연어 요청을 분석하여 Git 자동화 파이프라인이 실행할 수 있는 정형화된 명세(JSON)로 변환하는 역할을 수행합니다.

### 주요 역할
- **사용자 의도(Intent) 분석**: 입력 텍스트를 해석하여 작업 유형(문서화, 코드 수정, 트러블슈팅 작성 등)을 파악합니다.
- **컨텍스트 및 대상 매핑**: 입력 내용을 기반으로 대상 저장소(`kum`, `tetris-loop`, `gem-bridge`) 및 적절한 파일 경로(`target_path`)를 결정합니다.
- **마크다운 문서 생성**: 요구사항 및 상세 내용을 손실 없이 파싱하여 `content` 필드에 포함될 마크다운 문서로 변환합니다.
- **커밋 메시지 자동 생성**: 변경 사항을 명확히 설명하는 Conventional Commits 규격의 커밋 메시지를 생성합니다.

---

## 2. JSON 스키마 강제 방식 (JSON Schema Enforcement)

`intent_analyzer.py`는 외부 시스템 및 Git 파이프라인과의 안정적인 연동을 위해 LLM 응답이 지정된 JSON 스키마를 엄격히 준수하도록 다층 검증 구조를 사용합니다.

### 2.1. 프롬프트 명세 강제 (Prompt-level Enforcement)
- **지시문 명확화**: 시스템 프롬프트를 통해 오직 파싱 가능한 단일 JSON 개체만 반환하도록 제한하며, 마크다운 주석이나 설명 텍스트 포함을 금지합니다.
- **스키마 구조 명시**: 필수 키(`repo`, `target_path`, `content`, `commit_message`) 및 허용되는 값 영역(예: 저장소 목록 제한)을 엄격히 지정합니다.

### 2.2. 구조화된 출력 및 파싱 (Structured Output & Parsing)
- **API 레벨 제어**: LLM API 호출 시 JSON 모드(`response_format={'type': 'json_object'}`)를 지정하여 문법적으로 유효한 JSON 출력을 유도합니다.
- **후처리 정제**: 응답 문자열에 포함될 수 있는 불필요한 코드 블록 태그(` ```json `)나 이스케이프 문자를 정제하는 전처리 로직을 거칩니다.

### 2.3. 무결성 검증 및 예외 처리 (Validation & Retry)
- **JSON 문법 검증**: `json.loads()`를 통해 기본적인 Syntactic Validation을 진행합니다.
- **Schema/Pydantic Validation**: 정의된 데이터 구조와의 일치 여부, 필수 필드 존재 여부, 타입 타당성을 검증합니다.
- **재시도 메커니즘**: 파싱 또는 검증 실패 시, 오류 원인을 포함한 피드백 프롬프트를 LLM에 재전송하여 자동으로 올바른 구조를 재생성하도록 유도합니다.
# 개발 및 사용자 가이드

본 가이드는 애플리케이션의 안정적인 운용과 원활한 사용자 경험을 제공하기 위한 종합 지침서입니다. gem-bridge 프로젝트의 목적, 설치, 사용법, 아키텍처 및 업데이트/제거 절차와 시스템의 신뢰성을 높이기 위한 에러 핸들링 전략, 모바일 최적화 팁을 상세히 다룹니다.

---

## 1. 프로젝트 목적 (Project Purpose)

**gem-bridge**는 다양한 외부 서비스, 거대 언어 모델(LLM) API, 그리고 사내/외 비즈니스 애플리케이션 간의 유연하고 안정적인 데이터 중계를 처리하는 고성능 게이트웨이 및 브릿지 솔루션입니다. 

### 핵심 목표
- **시스템 간 결합도 완화**: 다대다(M:N)로 복잡하게 얽힌 API 호출 구조를 단일 진입점(Single Point of Entry)으로 표준화합니다.
- **트래픽 제어 및 안정성 보장**: 서킷 브레이커(Circuit Breaker) 및 속도 제한(Rate Limiting)을 도입하여 외부 API 장애가 내부 시스템으로 전파되는 것을 방지합니다.
- **실시간 프로토콜 변환**: HTTP/REST, WebSockets, gRPC 등 다양한 프로토콜 간의 원활한 실시간 메시지 변환 및 데이터 파이프라인 처리를 지원합니다.

---

## 2. 시스템 아키텍처 및 동작 구조 (Architecture & Operation)

gem-bridge는 클라이언트와 외부 대상 시스템 사이의 미들웨어 역할을 수행하며, 비동기 큐와 분산 처리 구조를 활용하여 설계되었습니다.

### 2.1 아키텍처 다이어그램

```
┌────────────────────────────────────────────────────────┐
│                   Client Applications                  │
└───────────────────────────┬────────────────────────────┘
                            │ (HTTP / WebSocket)
                            ▼
┌────────────────────────────────────────────────────────┐
│                      gem-bridge                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │                 API Gateway                      │  │
│  │   - Auth Verification & Rate Limiter             │  │
│  └────────────────────────┬─────────────────────────┘  │
│                           │ (Internal Event Routing)   │
│                           ▼                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │              Message Transformer                 │  │
│  │   - Payload Parsing & Protocol Conversion        │  │
│  └────────────────────────┬─────────────────────────┘  │
│                           │                            │
│                           ▼                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │               Queue & Retry Manager              │  │
│  │   - In-memory Buffer & Backoff Dispatcher       │  │
│  └────────────────────────┬─────────────────────────┘  │
└───────────────────────────┼────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│             External Services / LLM Providers          │
└────────────────────────────────────────────────────────┘
```

### 2.2 동작 원리 및 흐름
1. **요청 수신**: 클라이언트가 `gem-bridge` 엔드포인트로 데이터를 송신하면 Gateway 레이어에서 API 키 인증 및 요청 제한(Rate Limit)을 검증합니다.
2. **프로토콜 및 페이로드 변환**: `Message Transformer`가 수신된 공통 데이터 포맷을 수신 대상 서비스의 사양에 맞추어 변환하고 가공합니다.
3. **메시지 큐잉 및 디스패치**: 대량 트래픽 혹은 외부 대상의 일시적 지연에 대비해 비동기 대기열에서 메시지를 관리하며, `Queue Manager`를 통해 순차적/병렬적 배출을 진행합니다.
4. **결과 피드백 및 로깅**: 대상 시스템으로부터 전달받은 응답 결과를 다시 공통 규격으로 역변환하여 호출자에게 제공하고, 전체 생명 주기를 모니터링 로그에 기록합니다.

---

## 3. 설치 가이드 (Installation Instructions)

gem-bridge는 표준 패키지 관리자 및 컨테이너 환경을 통해 신속하게 구축할 수 있습니다.

### 3.1 사전 요구 사항
- **Node.js**: v18.0.0 이상
- **npm** 또는 **yarn** 최신 버전
- (선택 사항) **Docker**: 20.10 이상 (컨테이너 환경 배포 시)

### 3.2 소스 기반 설치 단계
1. **저장소 복제**
   ```bash
   git clone https://github.com/al-hub/gem-bridge.git
   cd gem-bridge
   ```

2. **의존성 패키지 설치**
   ```bash
   npm install
   # 또는 yarn 설치 시
   yarn install
   ```

3. **환경 변수 구성**
   제공된 `.env.example` 파일을 복사하여 실제 환경 변수를 구성합니다.
   ```bash
   cp .env.example .env
   ```
   `.env` 파일을 편집하여 필요한 포트 정보 및 연결 API 키를 입력하세요.

### 3.3 Docker 기반 설치 단계
```bash
# Docker 이미지 빌드
docker build -t gem-bridge:latest .

# 컨테이너 실행
docker run -d -p 8080:8080 --name gem-bridge-app --env-file .env gem-bridge:latest
```

---

## 4. 사용 가이드라인 (Usage Guidelines)

### 4.1 애플리케이션 실행
- **개발 모드**: 코드 변경 사항이 실시간으로 반영되는 핫 리로드 실행입니다.
  ```bash
  npm run dev
  ```
- **프로덕션 빌드 및 실행**: 최적화된 컴파일 버전을 생성하고 구동합니다.
  ```bash
  npm run build
  npm run start
  ```

### 4.2 기본 구성 파일 설정 (`config.json`)
시스템의 동작 방식을 재정의하는 구성 설정 파일 구조 예시입니다.
```json
{
  "server": {
    "port": 8080,
    "timeout": 30000
  },
  "features": {
    "enableRateLimit": true,
    "enableQueue": true
  },
  "retry": {
    "maxAttempts": 3,
    "backoffMs": 1000
  }
}
```

### 4.3 기본 테스트 호출 예제
로컬 인스턴스에 올바르게 연동되었는지 상태 진입점을 확인합니다.
```bash
curl -X GET http://localhost:8080/health
```

---

## 5. 업데이트 및 제거 절차 (Update and Removal)

### 5.1 업데이트 가이드 (Update Procedure)
최신 릴리즈의 성능 향상 및 보안 패치를 적용하기 위한 안전 가이드입니다.

#### 소스 수동 업데이트:
1. 실행 중인 프로세스를 중지합니다.
   ```bash
   npm run stop # 또는 pm2 stop gem-bridge
   ```
2. 최신 코드를 pull 받습니다.
   ```bash
   git checkout main
   git pull origin main
   ```
3. 의존성 패키지를 갱신합니다.
   ```bash
   npm ci
   ```
4. 빌드를 다시 진행한 후 기동합니다.
   ```bash
   npm run build
   npm run start
   ```

#### Docker 환경 업데이트:
```bash
# 최신 빌드 이미지 다운로드 및 컨테이너 재기동
docker pull alhub/gem-bridge:latest
docker stop gem-bridge-app
docker rm gem-bridge-app
docker run -d -p 8080:8080 --name gem-bridge-app --env-file .env alhub/gem-bridge:latest
```

### 5.2 시스템 제거 가이드 (Removal Procedure)
시스템 자원을 정리하고 gem-bridge를 온전히 폐기해야 할 때 아래 지침을 따르십시오.

#### 소스 및 파일 영구 삭제:
```bash
# 디렉토리 통째로 삭제 (주의: 설정 파일도 모두 삭제됩니다)
rm -rf /path/to/gem-bridge
```

#### Docker 리소스 해제:
```bash
# 컨테이너 중지 및 제거
docker stop gem-bridge-app
docker rm gem-bridge-app

# 사용하지 않는 이미지 및 볼륨 정리
docker rmi gem-bridge:latest
```

---

## 6. 에러 핸들링 (Error Handling)

안정적인 서비스를 위해 시스템 전반에서 발생할 수 있는 에러를 체계적으로 관리합니다.

### 6.1 에러 분류 및 계층 구조
- **네트워크 에러 (Network Error)**: 요청 시간 초과(Timeout), 연결 끊김, 서버 응답 없음 등.
- **인증/인가 에러 (Authentication & Authorization)**: 401 Unauthorized, 403 Forbidden.
- **비즈니스 로직 에러 (Business Logic Error)**: 유효성 검사 실패, 자원 미존재(404) 등.
- **시스템 에러 (System Error)**: 500 Internal Server Error, 메모리 부족, 예외 처리되지 않은 데이터 구조 등.

### 6.2 클라이언트 측 대응 전략
- **전역 에러 바운더리 (Global Error Boundary)**: React/Vue 등의 프레임워크 사용 시 컴포넌트 트리 상단에서 치명적인 에러를 포착하여 폴백(Fallback) UI를 제공합니다.
- **자동 재시도 (Exponential Backoff Retry)**: 일시적인 네트워크 장애 시 지수 백오프 알고리즘을 적용하여 최대 3회 재요청을 수행합니다.
- **사용자 친화적 메시지 제공**: 기술적인 모나드/스택 트레이스 노출을 지양하고, "네트워크 연결 상태를 확인하고 다시 시도해 주세요."와 같이 명확한 행동 지침을 안내합니다.

### 6.3 서버 및 API 에러 규격
모든 API 응답 오류는 표준화된 JSON 포맷을 준수합니다.

```json
{
  "success": false,
  "error": {
    "code": "INVALID_INPUT_VALUE",
    "message": "입력값의 형식이 올바르지 않습니다.",
    "details": [
      {
        "field": "email",
        "reason": "이메일 형식이 유효하지 않습니다."
      }
    ]
  },
  "timestamp": "2023-10-25T12:00:00Z"
}
```

### 6.4 모니터링 및 로깅
- **Sentry / Datadog 연동**: 실시간으로 발생한 에러 스택 트레이스를 모니터링 툴에 전송합니다.
- **로그 레벨 분리**: `DEBUG`, `INFO`, `WARN`, `ERROR`, `FATAL` 단계를 엄격히 구분하여 모니터링 피로도를 낮춥니다.

---

## 7. 모바일 최적화 및 팁 (Mobile Tips)

모바일 브라우저 및 하이브리드 앱 환경에서 최상의 사용자 경험(UX)과 성능을 확보하기 위한 최적화 지침입니다.

### 7.1 터치 및 인터랙션 최적화
- **터치 지연 제거**: `touch-action: manipulation;` CSS 속성을 적용하여 모바일 브라우저의 300ms 클릭 지연을 방지합니다.
- **터치 영역 확보**: 클릭 가능한 버튼 및 아이콘은 최소 `44px x 44px` 이상의 영역을 유지해야 합니다.
- **제스처 및 오버스크롤 관리**: 의도치 않은 이탈(예: iOS 뒤로가기 제스처) 방지가 필요한 페이지는 `overscroll-behavior: contain;`을 적용합니다.

### 7.2 뷰포트 및 반응형 레이아웃
- **동적 뷰포트 단위 사용**: 모바일 브라우저의 주소창 감춤/노출에 대응하기 위해 `100vh` 대신 `100dvh` (Dynamic Viewport Height)를 사용합니다.
- **안전 영역(Safe Area) 대응**: 아이폰 노치 디자인 대응을 위해 CSS 환경 변수를 적용합니다.
  ```css
  padding-top: env(safe-area-inset-top);
  padding-bottom: env(safe-area-inset-bottom);
  ```

### 7.3 성능 및 네트워크 최적화
- **이미지 및 미디어 오프로딩**: WebP/AVIF 포맷을 사용하고 `loading="lazy"` 속성으로 비동기 로딩을 유도합니다.
- **네트워크 상태 변화 감지**: `navigator.onLine` API 및 `online/offline` 이벤트 리스너를 활용해 오프라인 상태 시 적절한 경고 토스트를 노출합니다.
- **가상 리스트(Virtual Scroll) 도입**: 모바일 디바이스의 제한된 메모리를 고려하여 대량의 리스트 출력 시 DOM 절약을 위한 가상 스크롤을 적용합니다.

### 7.4 키보드 및 폼 처리
- **소프트 키보드 대응**: 입력 필드 focus 시 화면 요소를 가리지 않도록 `scrollIntoView()` 처리를 추가합니다.
- **입력 타입 명시**: `inputmode` 속성(`numeric`, `decimal`, `email` 등)을 적절히 사용하여 각 데이터 타입에 맞는 키패드가 노출되도록 합니다.

---

## 8. 문제 해결 FAQ

**Q. 모바일 환경에서 화면이 덜컹거리거나 스크롤이 불안정합니다.**
> CSS `overflow-y: auto;` 항목에 `-webkit-overflow-scrolling: touch;` 속성을 적용하고 layout thrashing을 일으키는 JS 뷰포트 계산 로직이 없는지 확인해 보세요.

**Q. 에러 로그가 반복해서 과도하게 수집됩니다.**
> 동일한 에러 유형의 경우 디바운싱(Debounce) 및 중복 제거 알고리즘을 로깅 모듈에 적용하여 핑(Ping) 폭주를 차단하세요.
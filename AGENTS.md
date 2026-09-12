# Agent Guidelines & Repository Rules

## 🔒 버전 관리 정책 (Strict Versioning Rule)
1. **Major Version Lock (v2 영구 고정)**:
   - 사용자의 명시적 허락이 있기 전까지는 **Major 버전(v3.0.0 등) 판올림을 원천 금지**합니다.
2. **Minor 버전 업데이트 원칙**:
   - 신규 기능 추가, 구조 개선, 프로토콜 확장 등 향후 모든 업데이트는 **Minor 버전(`v2.x.0`) 단위**로만 업데이트합니다.
3. **Single Source of Truth**:
   - 버전 갱신 시 `core/__version__.py`와 `VERSION`, `CHANGELOG.md`, `README.md`를 동시에 일치시킵니다.

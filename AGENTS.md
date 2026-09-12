# Agent Guidelines & Repository Rules

## 🔒 버전 관리 정책 (Strict Versioning Rule)
1. **버전 고정 (v2.1 고정)**:
   - 사용자의 명시적 허락이 있기 전까지는 **1번째(Major), 2번째(Minor) 자리는 절대 올리지 않고 고정**합니다.
2. **3번째 자리(z)만 업데이트 (v2.1.z)**:
   - 향후 모든 업데이트는 **3번째 자리인 `z` 단위(`v2.1.1`, `v2.1.2`, `v2.1.3`...)로만 업데이트**합니다.
3. **Single Source of Truth**:
   - 버전 갱신 시 `core/__version__.py`와 `VERSION`, `CHANGELOG.md`, `README.md`를 동시에 일치시킵니다.

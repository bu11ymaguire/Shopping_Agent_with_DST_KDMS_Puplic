# Reference archive

`reference/mock-demo-v1/`은 실데이터·LLM 전환 이전에 사용했던 Next.js 목업 데모의
**읽기 전용 원본 스냅숏**이다. 현행 애플리케이션이나 `backend/`의 런타임 소스가 아니다.

## 보존하는 이유

- 3패널 UI와 SPN/RA-Rec 색상·노드 표현을 재구성할 수 있다.
- 6턴 iPhone 시나리오의 상품·리뷰 fixture와 기대 순위를 회귀 비교할 수 있다.
- State Manager, Policy, Query, review/product ranking의 이전 순수 함수 구현을 확인할 수 있다.
- 아키텍처 HTML, 사례 HTML, mock 상품 SVG 등 `flow.md`의 요약만으로 복원하기 어려운
  시각·실행 자료를 보존한다.

## 우선순위와 사용 규칙

1. 현재 사용자 지시
2. 루트 `AGENTS.md`
3. 루트 `flow.md`의 계약과 교정 사항
4. 루트 `manual.md`의 기술·데이터 권고
5. 이 목업 스냅숏

목업과 `flow.md`가 충돌하면 `flow.md`를 따른다. 명시적인 요청 없이 스냅숏을
수정하거나 현행 빌드에 연결하지 않는다. 필요한 코드는 현행 위치로 옮겨 재구현하고,
목업의 알려진 버그·하드코딩·fixture 전용 상수를 함께 복사하지 않는다.

## 스냅숏 안의 세대 구분

현재 목업 실행 경로였던 파일:

- `app/page.tsx`
- `lib/types.ts`
- `lib/spn/*`
- `lib/rarec/*`
- `lib/mock/*`
- `data/products.json`, `data/reviews.json`
- `episode/spn-ra-rec-architecture-annotated.html`
- `episode/iPhone.html`

더 오래된 태블릿 프로토타입 참고 파일:

- `data/scenario.json`
- `app.js`
- `index.html`
- `styles.css`

태블릿 프로토타입의 `accepted_items`와 위험성 추론 등은 현재 6턴 iPhone 회귀 계약이
아니다. 태블릿을 실제 품목으로 채택할지는 `manual.md`의 데이터 프로파일링 이후 결정한다.

## 재현

목업 자체를 확인해야 할 때만 다음을 실행한다.

```powershell
cd reference\mock-demo-v1
npm ci
npm run dev
```

검증 명령은 `npx tsc --noEmit`, `npm run lint`, `npm run build`다. 이 중첩 프로젝트의
의존성은 루트나 `backend/`에 설치하지 않는다.

이 스냅숏은 2026-08-06에 사용자가 제공한 54개 파일을
`reference/mock-demo-v1/` 아래로 이동해 보존한 것이다. Git import 검사에서 발견된
`additional.txt` 한 줄의 후행 공백만 제거했으며 실행·문서 의미는 변경하지 않았다.

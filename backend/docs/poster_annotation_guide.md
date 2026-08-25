# 포스터 평가 판정자 운영 가이드

상태: v1 판정 세션 준비 완료, human annotation 미실행

기준일: 2026-08-08

이 문서는 동결된 `poster_annotation_v1`을 3명의 판정자에게 독립적으로 배정하기 위한 운영
절차다. 연구 질문과 지표의 정본은 `poster_evaluation_protocol.md`이며, 이 문서는 판정 중 실수를
줄이기 위한 체크리스트다.

## 판정자에게 전달할 것

- 각 판정자에게 본인의 `annotator-NN/sessions/` 폴더만 전달한다.
- `private_provenance.json`, 다른 판정자의 폴더, 시스템 이름·점수·원래 순위는 전달하지 않는다.
- 상품과 리뷰 판정을 섞지 않는다. 기본 분할은 1인당 상품 2세션과 리뷰 3세션이며 각 세션은
  120건 이하다.

## 시작 전 연습

- 실제 holdout과 겹치지 않는 5~10개 항목으로 rubric을 연습한다.
- 연습 항목은 토론할 수 있지만 실제 holdout 판정은 서로 보지 않고 독립적으로 수행한다.
- 연습 결과를 본 뒤 production prompt·retriever·ranker나 동결된 holdout rubric을 바꾸지 않는다.
- rubric 자체가 불명확한 경우에만 본 판정 시작 전에 설명을 고정하고 변경 이력을 남긴다.

## 판정 방법

1. `product-session-NN.json`에서는 각 후보의 `annotation.relevance`를 0~3으로 채우고,
   `annotation.rationale`에 짧은 근거를 쓴다.
2. `review-session-NN.json`에서도 같은 두 필드를 채운다. 리뷰는 문법이나 별점이 아니라 사용자의
   soft need를 실제로 뒷받침하는지를 판정한다.
3. ID, 시나리오, 상품 metadata, 리뷰 본문 등 `annotation` 밖의 내용은 수정하지 않는다.
4. 세션 하나를 끝낸 뒤 저장하고 쉬는 것을 권장한다. 미완료 세션은 병합되지 않는다.

상품과 리뷰의 상세 0~3 기준은 `poster_evaluation_protocol.md` §5를 따른다.

## 완료와 병합

판정자는 자신의 5개 세션을 모두 제출한다. 관리자는 세 판정자의 파일을 원래 작업 폴더에 놓은
뒤 다음 명령을 실행한다.

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\merge_poster_annotation_sessions.py
```

병합기는 모든 label·rationale, 항목 coverage, session manifest hash를 확인한다. 비라벨 내용 변경,
누락·중복, 부분 완료, 기존 완료 packet과 다른 label 덮어쓰기를 거부한다. 성공하면 세션의 label만
각 판정자의 `product_annotations.json`과 `review_annotations.json`에 반영한다.

그 다음 `collection_manifest.json`에 수집 방식, 독립 판정 여부, private provenance 비공개 여부,
완료 시각과 작성 주체 고지를 사실대로 기록한다. 개인식별정보는 넣지 않는다. 마지막으로 다음을
실행한다.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_poster_annotations.py
```

집계 결과가 나와도 현재 v1은 token/semantic pool이므로 RQ2·RQ3의 보조 결과까지만 해석한다.
RQ1의 Full/No-memory 및 State Diff 평가는 별도 동결 입력이 필요하다.

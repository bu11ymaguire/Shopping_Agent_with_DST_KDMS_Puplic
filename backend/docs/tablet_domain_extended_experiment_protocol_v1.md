# Extended Experiment v1: Full-Memory False Update 비교 규약

## 목적과 주장 범위

이 실험은 Full-Memory가 현재 턴의 Gold State Diff에 없는 과거 상태를 다시
update 후보로 출력하는 현상을 줄이면서, 누적 Final State와 hard-filter 동작을
보존하는 방법을 비교한다. 여기서는 이를 작업 용어로
**Previous-State-Induced False Update(PSIFU)**, 그중 과거 carryover를 현재 update로
오인한 경우를 **Carryover-to-Update Confusion(C2U)**이라고 부른다. 이 명칭을 기존
표준 용어라고 주장하지 않는다.

현재 20개·81턴 데이터는 이미 공식 실험에서 관찰됐으므로 이번 결과는
`posthoc_exploratory_not_confirmatory`다. 방법 선정 결과는 새 untouched holdout을
실행 전에 동결하고 한 번 평가하기 전까지 최종 결과로 승격하지 않는다. 기존 official
raw, automatic benchmark, freeze manifest는 재실행하거나 수정하지 않는다.

## 비교군

각 arm은 하나의 독립적인 방법 가설이다. A+B+C 같은 결합 구조는 이번 비교에 넣지
않는다.

| ID | 방법 | 현재 구현에서 달라지는 한 요소 | 직접 근거/인접 근거 |
| --- | --- | --- | --- |
| `m0_baseline` | 현행 Full-Memory | frozen v2.3 Understanding 후보와 현행 merge | 공식 기준선 |
| `a_som_operation` | per-slot operation gate | 과거 active ID마다 `carryover/upsert/delete`를 명시하고 `upsert`만 후보화 | [SOM-DST](https://doi.org/10.18653/v1/2020.acl-main.53)에서 영감 |
| `b_sparse_delta` | sparse state operation | 현재 턴의 `upsert/delete`만 생성하고 deterministic manager가 적용 | [IC-DST](https://doi.org/10.18653/v1/2022.findings-emnlp.193), [Diable](https://doi.org/10.18653/v1/2023.findings-acl.615)에서 영감 |
| `c_semantic_noop` | deterministic semantic no-op | 현행 후보는 유지하되 상태와 의미적으로 동일한 재방출은 merge·changed path·provenance에서 제외 | 현재 오류의 deterministic lower-layer 대조군 |
| `d_full_state_diff` | full state → deterministic diff | LLM이 턴 이후 전체 active snapshot을 만들고 코드가 prior snapshot과 diff | full-state 생성 대조군 |
| `e_compact_context` | selective prior context | hard correction 값, qualitative ID, rank context만 제공하고 provenance-heavy object를 제거 | [NLSI](https://doi.org/10.18653/v1/2024.findings-naacl.255), [ReacTOD](https://doi.org/10.18653/v1/2026.trustnlp-main.21)의 인접 아이디어 |

논문의 알고리즘을 동일하게 재현한다고 주장하지 않고 `*-inspired` 구현으로 보고한다.
예를 들어 A는 SOM-DST의 학습된 분류기 대신 GPT-4o-mini strict structured output으로
operation을 생성한다.

## 고정되는 공통 경로

- 동일한 20 scenario·81 turn utterance와 Gold State Diff/Final State
- 동일한 GPT-4o-mini transport, temperature 0, strict schema/repair 정책
- 동일한 `DialogueState`, Policy, Query Generator
- 동일한 117개 상품·7,552개 리뷰 catalog
- 동일한 semantic bi-encoder → Cross-Encoder → Rank
- 조건당 scenario 순서와 turn 순서
- 모든 arm에서 Understanding LLM 호출은 턴당 1회
- Response Composer는 deterministic template로 고정하여 state 실험과 무관한 두 번째
  LLM 호출을 제거

기본 전체 실행의 예상 Understanding 호출 수는 `81 × 6 = 486`이다. 인프라 오류
(timeout, 429, 5xx, connection)에만 턴당 최대 2회 외부 retry를 허용한다. schema-valid
semantic error나 낮은 점수를 이유로 재실행하지 않는다. 모든 arm이 끝나기 전에는
집계하거나 승자를 선택하지 않는다.

## 오류 계층과 지표

State Diff F1 하나만 보면 C처럼 후보 FP는 그대로인데 material change만 억제한 경우를
놓친다. 따라서 다음을 분리한다.

모든 primary automatic 분모는 81턴이다. schema/transport 오류 뒤 출력되지 않은 턴은
candidate와 State Diff의 Gold operation을 모두 FN, hard-filter exact를 실패로 센다.
완료 출력만으로 계산한 값도 diagnostic으로 함께 남긴다.

1. **Candidate extraction**: 현재 턴 Gold candidate ID 대비 precision/recall/F1과 FP 수
2. **C2U candidate FP**: prior active ID를 현재 후보로 재출력했지만 Gold current update가
   아닌 ID 수
3. **Material State Diff**: 실제 merge 후 Gold operation 대비 precision/recall/F1
4. **Final State**: episode 종료 시 active canonical ID micro-F1
5. **Correction retention**: `th03:4`, `th17:3`, `th18:3`의 candidate recall
6. **Downstream retention**: hard-filter completion, Policy, recommendation reach
7. **Operational**: scenario/schema completion, median Understanding latency, token 수,
   operation contract violation, silent delete 수

`candidate FP 감소`와 `material State Diff FP 감소`는 별도 결과다. 전자는 Understanding
개선이고 후자는 State Manager의 정확한 변화 계측 개선이다.

## 사전 선택 규칙

M0 대비 다음 세 gate를 먼저 모두 통과해야 한다.

- Final State micro-F1: M0보다 최대 0.02 낮음까지 허용
- correction candidate recall: M0보다 최대 0.05 낮음까지 허용
- hard-filter completion: M0보다 최대 0.02 낮음까지 허용
- turn-output completion: M0보다 최대 0.02 낮음까지 허용

통과 arm만 다음 lexicographic 순서로 정렬한다.

1. State Diff micro-F1 최대
2. C2U candidate FP 최소
3. median Understanding latency 최소
4. strategy ID 순서(완전 동률 재현용)

이 규칙이 선정한 값은 `exploratory_selected_strategy`로만 기록한다. 새 untouched
holdout에서는 이 arm과 M0만 고정해 confirmatory 비교한다. 필요하면 그 다음 단계에서
선정 arm과 C를 결합하는 component ablation을 별도 연구로 수행한다.

## Gold-State Oracle 추천 비교

기존 `tablet-domain-gold-state-oracle-rankings-posthoc-v1` compact JSON을 `--oracle`로
주면 각 arm의 마지막 Top-3와 Gold DialogueState를 동일 추천기에 넣은 Top-3를 비교한다.
Top-1 일치, ordered/set Top-3, overlap, Jaccard를 secondary diagnostic으로 기록한다.

이 Oracle은 Gold 상품, 인간 relevance label, 추천 품질 정답이 아니다. 따라서 방법 선정
gate에는 사용하지 않으며 “Gold DialogueState를 입력받은 현재 recommender 출력에 얼마나
가까운가”만 말할 수 있다.

## 실행과 산출물

```powershell
cd backend
python scripts\verify_tablet_domain_extended_experiment.py
python scripts\run_tablet_domain_extended_experiment.py `
  --oracle <gold-state-oracle-compact-json>
```

- raw/checkpoint: `reports/tablet_domain_extended_experiment_posthoc_v1.json`
- compact tracked summary: `data/results/tablet_domain_extended_experiment_posthoc_v1.json`
- LLM trace: `logs/tablet_extended_<run-id>_<strategy>.jsonl`

raw와 trace는 Git 제외다. compact result에는 raw hash, dataset hash, commit, prompt version,
strategy별 schema/metric/token summary, 선택 gate, 한계를 남긴다.

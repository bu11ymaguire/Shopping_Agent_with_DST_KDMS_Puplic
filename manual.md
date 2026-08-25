이 프로젝트에는 **LangGraph를 중심으로 한 Python 백엔드**가 가장 잘 맞습니다. 다만 “여러 에이전트가 자율적으로 협업하는 multi-agent 시스템”으로 만들기보다는, 현재 설계한 SPN–RA-Rec 모듈을 **명시적인 노드와 조건부 분기로 구현한 stateful workflow**로 만드는 편이 좋습니다.

LangGraph는 장기 실행·상태 유지형 워크플로를 위한 저수준 오케스트레이션 프레임워크이며, 결정론적으로 작성한 단계와 LLM 기반 단계를 하나의 그래프 안에서 혼합할 수 있습니다. 또한 LangChain 없이도 독립적으로 사용할 수 있습니다. ([Docs by LangChain][1])

# 1. 사용할 데이터는 네 층으로 나누는 것이 좋음

하나의 데이터셋에서 상품, 리뷰, 실시간 재고, 숨은 의도, 대화 정답까지 모두 얻기는 어렵습니다. 따라서 데이터 역할을 분리해야 합니다.

## 1.1 상품·리뷰: Amazon Reviews 2023

주 데이터는 **Amazon Reviews 2023의 `Electronics` 카테고리**를 사용하는 것이 적절합니다.

Amazon Reviews 2023은 리뷰 본문·평점·도움됨 수·구매 인증 여부와 상품명·설명·특징·가격·이미지·상세 속성·연관 상품 정보를 함께 제공합니다. 리뷰와 상품 메타데이터는 `parent_asin`으로 연결할 수 있습니다. ([Amazon Reviews][2])

전체 `Electronics`에는 약 160만 개 상품과 4,390만 개의 평점이 있어 그대로 사용하기에는 지나치게 큽니다. ([Hugging Face][3])

따라서 다음처럼 좁혀야 합니다.

```text
Amazon Reviews 2023 / Electronics
└─ metadata의 category path에서 tablet 관련 상품 필터
   └─ 메타데이터가 충분한 상품만 선택
      ├─ 상품명
      ├─ 가격
      ├─ 주요 사양
      ├─ 이미지
      └─ 리뷰 20개 이상
```

### 추천하는 첫 상품 영역

**태블릿**이 가장 적합합니다.

* 저장 공간, 무게, 화면, 배터리, 펜 지원 등 구조화할 사양이 많음
* 필기, 영상, 게임, 휴대성과 같은 사용 목적이 다양함
* 가격·성능·휴대성·저장 공간 간 트레이드오프를 만들기 쉬움
* 현재 데모 시나리오와 연결 가능함
* Best Buy에서도 유사 상품을 찾기 쉬움

첫 구현에서는 다음 정도만 추출하는 편이 현실적입니다.

| 항목        |           초기 규모 |
| --------- | --------------: |
| 상품        |        100~300개 |
| 상품당 리뷰    |         20~100개 |
| 전체 리뷰     | 약 5,000~20,000개 |
| 리뷰 aspect |           6~10개 |

리뷰는 무작위로 전부 가져오기보다 다음 조건으로 정리합니다.

```text
verified_purchase = True 우선
helpful_vote가 높은 리뷰 우선
긍정·부정 리뷰 모두 포함
동일하거나 지나치게 짧은 리뷰 제거
```

Amazon Reviews 2023에는 `verified_purchase`, `helpful_vote`, timestamp와 리뷰 본문이 포함되므로 이러한 필터링이 가능합니다. ([Hugging Face][3])

---

## 1.2 상품 검색 평가: Amazon ESCI

Amazon Reviews 2023은 실제 리뷰에는 좋지만, **검색 질의에 어떤 상품이 적합한지에 대한 정답 라벨**은 없습니다.

Query Generator와 Retrieval 모듈을 별도로 평가하려면 **Amazon Shopping Queries Dataset, 즉 ESCI 데이터셋**을 보조적으로 사용하는 것이 좋습니다.

ESCI는 하나의 검색 질의에 대해 최대 40개의 상품 결과와 다음 네 종류의 관련성 라벨을 제공합니다.

* Exact
* Substitute
* Complement
* Irrelevant

따라서 상품 검색과 순위화 평가에 직접 사용할 수 있습니다. ([GitHub][4])

용도는 다음처럼 분리합니다.

```text
Amazon Reviews 2023
→ 실제 상품·리뷰 근거
→ 리뷰 검색과 추천 설명

Amazon ESCI
→ Query Generator 및 상품 검색 평가
→ Recall@K, NDCG@K, ESCI 분류
```

두 데이터셋의 상품을 반드시 완전히 연결할 필요는 없습니다. ESCI는 **검색 모듈의 독립 평가 데이터**로 사용하면 됩니다.

---

## 1.3 가격·재고·배송: 통제된 운영 데이터 + 선택적 API

재현 가능한 평가는 직접 만든 운영 데이터로 구성합니다.

```json
{
  "product_id": "tablet_001",
  "price": 429.0,
  "stock_status": "in_stock",
  "stock_quantity": 3,
  "delivery_days": 1,
  "pickup_available": true,
  "snapshot_time": "2026-08-05T15:00:00+09:00"
}
```

턴이나 실험 조건에 따라 변경합니다.

```text
Turn 1: 재고 3개, 내일 배송
Turn 3: 품절
Turn 4: 다른 색상만 재입고
```

실제 환경 연결을 보여주고 싶을 때는 **Best Buy Products·Stores API**를 선택적으로 사용합니다. Best Buy API는 상품 가격, availability, 사양, 설명과 이미지 등을 제공하며, 상품과 매장 API를 결합해 매장 내 구매 가능 여부를 확인할 수 있습니다. 일부 정보는 near-real-time으로 갱신됩니다. ([Best Buy Developer][5])

다만 연구 평가에서는 외부 재고가 수시로 바뀌면 실험을 반복할 수 없으므로 역할을 다음처럼 나누는 것이 좋습니다.

```text
통제된 Inventory API
→ 정량 평가와 재현 실험

Best Buy API
→ 실제 환경 연결 가능성 데모
```

Amazon 상품과 Best Buy 상품은 식별자가 다르므로, 라이브 데모용 상품 20~30개 정도만 모델 번호·UPC·상품명으로 수작업 검증하여 연결하는 것이 현실적입니다.

---

## 1.4 다중 턴 대화: 직접 구축한 주석 데이터

기존 데이터셋에는 다음 정답이 없습니다.

* 숨은 의도 가설
* 턴별 Dialogue State
* State Diff
* CLARIFY·BROWSE·RECOMMEND 정답
* 거절 이후 기대되는 순위 변화

따라서 이 부분은 직접 구축해야 합니다.

```json
{
  "scenario_id": "tablet_urgency_01",
  "turn": 3,
  "user_utterance": "64GB는 오래 쓰기 어려울 것 같아요.",
  "gold_state_diff": {
    "rejected_item": "tablet_013",
    "rejection_reason": "storage",
    "storage_preference": "at_least_128gb"
  },
  "gold_action": "RECOMMEND",
  "latent_hypotheses": [
    {
      "content": "저장 공간을 낮은 가격보다 우선할 가능성",
      "status": "supported",
      "scope": "current_tablet_purchase"
    }
  ],
  "expected_ranking_change": {
    "tablet_013": "down",
    "tablet_028": "up"
  }
}
```

초기 목표는 다음 정도면 충분합니다.

```text
10개 사용자 유형
×
5개 상황 유형
=
50개 대화

각 대화 4~8턴
```

상황 유형은 예산 부족, 사용 목적 암시, 상품 거절, 선호 변화, 재고·배송 변화, 기존 조건 충돌 등으로 구성합니다.

---

# 2. LangChain과 LangGraph 중 무엇을 사용할까

## LangChain

LangChain은 모델 호출, tool calling, structured output, retriever 같은 **LLM 애플리케이션 구성요소를 연결하는 추상화 계층**입니다. 단순한 질의→검색→응답 흐름에는 편리합니다. ([GitHub][6])

하지만 현재 파이프라인은 단순한 일방향 체인이 아닙니다.

```text
Understanding
→ State Update
→ Policy
   ├─ 질문 → 사용자 입력 대기 → State Update
   ├─ 조회 → State Update → Policy 재실행
   └─ 추천 → 사용자 피드백 → State Update
```

여러 턴에 걸쳐 상태를 유지하고, 분기와 반복이 있으며, 특정 시점에 사용자 확인을 기다려야 합니다.

## LangGraph

이 구조에는 LangGraph가 더 잘 맞습니다.

LangGraph는 workflow와 agent를 구분하며, predetermined code path를 가진 workflow와 동적으로 도구를 선택하는 agent 패턴을 모두 지원합니다. 또한 persistence, streaming, debugging과 조건부 routing을 제공합니다. ([Docs by LangChain][7])

따라서 역할을 이렇게 정리하면 됩니다.

```text
LangGraph
→ 전체 파이프라인의 노드, 상태, 분기, 반복 관리

LangChain
→ 필요한 경우 LLM·도구·structured output 연동에만 사용
```

**LangChain을 반드시 같이 써야 하는 것은 아닙니다.** LangGraph 공식 문서도 LangGraph를 LangChain 없이 사용할 수 있다고 명시합니다. ([Docs by LangChain][1])

---

# 3. 권장 구현 구조

현재 Next.js 데모는 프론트엔드로 유지하고, 실제 파이프라인은 Python 백엔드로 분리하는 것이 좋습니다.

```text
Next.js Frontend
        │
        ▼
FastAPI Backend
        │
        ▼
LangGraph Workflow
        │
        ├─ SPN Understanding
        ├─ RA-Rec State Manager
        ├─ Latent Hypothesis Manager
        ├─ SPN Policy
        ├─ Query Generator
        ├─ Browsing Actions
        ├─ Recommendation Engine
        └─ Response Composer
        │
        ▼
PostgreSQL + pgvector
```

FastAPI는 Python type hint를 기반으로 API를 구축할 수 있고 비동기 호출을 지원하므로, LLM 호출·DB 검색·외부 상품 API 조회를 묶는 백엔드에 적합합니다. ([FastAPI][8])

---

# 4. LangGraph에서 각 모듈을 어떻게 구현할까

## Graph State

모든 노드가 공유하는 상태를 정의합니다.

```python
from typing import Literal
from pydantic import BaseModel, Field


class LatentHypothesis(BaseModel):
    hypothesis_id: str
    content: str
    content_confidence: float
    scope_confidence: float
    uncertainty: float
    ranking_impact: float
    status: Literal[
        "candidate",
        "supported",
        "confirmed",
        "rejected",
    ]
    evidence_ids: list[str] = Field(default_factory=list)


class ShoppingState(BaseModel):
    conversation_id: str
    turn: int
    user_message: str

    explicit_constraints: dict[str, object] = Field(default_factory=dict)
    soft_constraints: dict[str, object] = Field(default_factory=dict)

    rejected_items: list[str] = Field(default_factory=list)
    accepted_items: list[str] = Field(default_factory=list)

    latent_hypotheses: list[LatentHypothesis] = Field(
        default_factory=list
    )

    vagueness_score: float = 0.0
    next_action: Literal[
        "CLARIFY",
        "BROWSE",
        "RECOMMEND",
        "RESPOND",
    ] | None = None

    candidate_product_ids: list[str] = Field(default_factory=list)
    ranked_product_ids: list[str] = Field(default_factory=list)
```

Pydantic을 쓰면 각 노드의 입출력 스키마를 명확하게 유지하고, LLM이 잘못된 형식의 결과를 반환했을 때 검증할 수 있습니다. Pydantic Graph도 type hint를 이용해 노드와 상태를 정의하는 비동기 상태 머신을 제공합니다. ([Pydantic][9])

다만 현재 프로젝트에서는 자료와 사례가 더 많고 tracing 도구가 잘 갖춰진 **LangGraph를 우선 선택**하는 편이 좋습니다.

---

## Graph Node

각 기존 모듈을 하나의 노드로 옮깁니다.

```python
def understand(state: ShoppingState) -> dict:
    ...

def update_state(state: ShoppingState) -> dict:
    ...

def update_hypotheses(state: ShoppingState) -> dict:
    ...

def select_action(state: ShoppingState) -> dict:
    ...

def generate_query(state: ShoppingState) -> dict:
    ...

def browse_products(state: ShoppingState) -> dict:
    ...

def rank_products(state: ShoppingState) -> dict:
    ...

def compose_response(state: ShoppingState) -> dict:
    ...
```

Policy 결과에 따라 조건부 edge를 연결합니다.

```text
select_action
├─ CLARIFY  → compose_clarifying_question → END
├─ BROWSE   → generate_query → browse → rank
└─ RECOMMEND → rank → compose_recommendation → END
```

다음 사용자 입력이 오면 같은 conversation ID의 state를 다시 불러와 다음 턴을 실행합니다.

---

# 5. 모든 노드를 LLM으로 만들면 안 됨

이 프로젝트에서는 **LLM 기반 단계와 결정론적 단계를 명확하게 분리해야 합니다.**

## LLM을 사용하기 좋은 단계

```text
SPN Understanding
→ 발화에서 자연어 조건·거절 이유 추출

Latent Hypothesis Candidate Generation
→ 관찰을 설명할 수 있는 후보 가설 생성

Response Composer
→ 구조화된 추천 결과를 자연어로 표현
```

## 코드와 수식으로 구현할 단계

```text
State Manager
→ 상태 병합, provenance, State Diff

Confidence / Uncertainty
→ InterQuest 기반 수식

Policy
→ threshold와 우선순위 기반 분기

Query Generator
→ hard constraint를 검색 조건으로 변환

Recommendation Engine
→ 필터링, 점수 계산, 재순위화

Evaluation
→ 정답과 출력 비교
```

LangGraph는 결정론적 단계와 LLM 기반 단계를 같은 그래프에서 혼합하도록 설계되어 있기 때문에 이 구조에 잘 맞습니다. ([Docs by LangChain][1])

LLM에게 다음 행동 전체를 자유롭게 선택하게 하면 어느 규칙 때문에 분기가 발생했는지 평가하기 어려워집니다. 따라서 초기에는 **single agent + explicit workflow**로 구현하고, multi-agent 구조는 사용하지 않는 것이 좋습니다. LangChain 공식 문서 역시 복잡한 작업이라고 해서 반드시 multi-agent가 필요한 것은 아니라고 설명합니다. ([Docs by LangChain][10])

---

# 6. 검색과 추천에 사용할 라이브러리

## 데이터 전처리

### Hugging Face `datasets`

Amazon Reviews 2023 로딩에 사용합니다. `load_dataset()`은 Hub나 로컬 파일을 읽을 수 있고 streaming 형태도 지원합니다. ([Hugging Face][11])

### DuckDB

대형 Parquet 파일에서 필요한 카테고리와 열만 추출하는 데 사용합니다. DuckDB는 Parquet 파일을 직접 SQL로 조회하고 filter·column pushdown을 적용할 수 있습니다. ([DuckDB][12])

```text
datasets
→ 원본 다운로드·streaming

DuckDB
→ 상품·리뷰 필터링과 조인

Parquet
→ 전처리 결과 저장
```

처음부터 전체 데이터를 pandas에 올리지는 않는 것이 좋습니다.

---

## 리뷰 임베딩과 검색

### Sentence Transformers

* 상품 설명 및 리뷰 embedding
* 사용자 질의와 리뷰 간 semantic similarity
* Cross-Encoder를 사용한 재순위화

Sentence Transformers는 embedding 모델과 reranker 모델을 모두 지원하며 semantic search에 사용할 수 있습니다. ([Sbert][13])

흐름은 다음과 같습니다.

```text
사용자 상태를 자연어 검색 질의로 변환
→ 상품 메타데이터 hard filter
→ 리뷰 embedding 검색
→ 상위 리뷰 Cross-Encoder reranking
→ 상품별 리뷰 근거 점수 집계
```

---

## DB와 벡터 검색

### PostgreSQL + pgvector

상품 데이터에는 다음이 동시에 필요합니다.

* 가격 범위
* 저장 공간
* 무게
* 브랜드
* 재고 여부
* 배송 기한
* 리뷰 semantic similarity

따라서 vector database만 따로 쓰기보다 PostgreSQL에 `pgvector`를 추가하는 것이 초기 프로젝트에는 적절합니다. pgvector는 PostgreSQL 안에서 exact·approximate nearest-neighbor 검색과 cosine distance 등을 지원하고, 일반 상품 속성과 vector를 함께 저장할 수 있습니다. ([GitHub][14])

예:

```sql
SELECT *
FROM reviews
WHERE product_id IN (
    SELECT id
    FROM products
    WHERE price <= 500
      AND storage_gb >= 128
      AND delivery_days <= 3
)
ORDER BY embedding <=> :query_embedding
LIMIT 20;
```

초기 데모가 100~300개 상품이라면 FAISS를 도입할 필요도 없습니다. PostgreSQL + pgvector만으로 구현과 로그 관리가 모두 가능합니다.

---

# 7. 로깅과 평가 라이브러리

## 자체 로그를 우선 저장

연구에서 가장 중요한 것은 framework UI가 아니라 **재현 가능한 원본 로그**입니다.

각 노드 실행마다 다음을 DB 또는 JSONL에 저장합니다.

```json
{
  "conversation_id": "c001",
  "turn": 3,
  "node": "state_manager",
  "input_state_hash": "...",
  "output": {},
  "state_diff": {},
  "latency_ms": 141,
  "model": null,
  "prompt_version": null,
  "timestamp": "..."
}
```

이 로그를 Pipeline Inspector가 읽도록 만듭니다.

## LangSmith는 선택적으로 사용

LangSmith는 trace 시각화, evaluation dataset 관리, 버전 비교와 regression evaluation을 지원합니다. 특정 node나 전체 workflow를 대상으로 평가할 수도 있습니다. ([Docs by LangChain][15])

따라서 다음처럼 사용합니다.

```text
PostgreSQL / JSONL 로그
→ 연구 결과의 source of truth

LangSmith
→ 개발 중 trace 확인, 디버깅, 실험 비교
```

LangSmith에만 로그를 의존하지 않는 것이 좋습니다. 나중에 비용이나 계정 문제 없이 실험 데이터를 재분석할 수 있어야 하기 때문입니다.

---

# 8. 최종 권장 기술 스택

| 영역                     | 선택                                            |
| ---------------------- | --------------------------------------------- |
| 프론트엔드                  | 기존 Next.js                                    |
| 백엔드 API                | FastAPI                                       |
| 파이프라인 오케스트레이션          | **LangGraph**                                 |
| 상태·스키마 검증              | Pydantic                                      |
| LLM 연결                 | 공급자 SDK 또는 필요한 LangChain model/tool component |
| 원본 데이터 로딩              | Hugging Face `datasets`                       |
| 대형 데이터 전처리             | DuckDB + Parquet                              |
| 상품·상태·로그 저장            | PostgreSQL                                    |
| 벡터 검색                  | pgvector                                      |
| 리뷰 embedding·reranking | Sentence Transformers                         |
| 개발 중 tracing           | LangSmith 선택 사용                               |
| 정량 평가                  | Python 자체 evaluator + scikit-learn            |
| 실시간 상품 정보              | Best Buy API 선택 연동                            |
| 배포                     | Docker Compose                                |

설치 패키지를 개략적으로 적으면 다음과 같습니다.

```text
langgraph
langchain-core
pydantic
fastapi
uvicorn
datasets
duckdb
pyarrow
psycopg
pgvector
sentence-transformers
scikit-learn
httpx
pytest
```

# 9. 데이터와 구현에 대한 잠정 결정안

현재 단계에서는 다음 구성으로 정하는 것이 가장 안정적입니다.

## 핵심 데이터

```text
Amazon Reviews 2023 / Electronics
→ 태블릿 상품 100~300개
→ 실제 리뷰 5,000~20,000개
```

## 검색 평가 데이터

```text
Amazon ESCI
→ Query Generator와 Product Retrieval 독립 평가
```

## 동적 정보

```text
자체 Inventory Snapshot 데이터
→ 정량 평가

Best Buy API
→ 실제 환경 연동 데모
```

## 대화 데이터

```text
직접 주석한 50개 다중 턴 시나리오
→ State / Diff / Policy / Hypothesis / Ranking 정답
```

## 프레임워크

```text
Next.js
+
FastAPI
+
LangGraph
+
PostgreSQL / pgvector
```

이 조합을 쓰면 현재 문서의 각 모듈이 거의 그대로 실제 코드 구조가 됩니다.

```text
SPN Understanding         → LangGraph Node
RA-Rec State Manager      → LangGraph Node + PostgreSQL
SPN Policy                → Conditional Edge
Query Generator           → deterministic function
Browsing Actions          → pgvector / Best Buy tool
Recommendation Engine     → filter + retrieval + ranking
Response Composer         → LLM Node
Pipeline Inspector        → 저장된 node trace 시각화
```

가장 먼저 할 일은 Amazon Reviews 2023의 `Electronics` 메타데이터를 일부만 읽어 **태블릿 상품이 실제로 얼마나 깔끔하게 분리되는지 확인하는 데이터 프로파일링**입니다. 이 결과를 본 뒤 태블릿을 유지할지, 스마트폰이나 다른 하위 카테고리로 전환할지를 확정하는 순서가 좋습니다.

[1]: https://docs.langchain.com/oss/python/langgraph/overview "LangGraph overview - Docs by LangChain"
[2]: https://amazon-reviews-2023.github.io/ "Amazon Reviews'23"
[3]: https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023 "McAuley-Lab/Amazon-Reviews-2023 · Datasets at Hugging Face"
[4]: https://github.com/amazon-science/esci-data?utm_source=chatgpt.com "amazon-science/esci-data: Shopping Queries Dataset: A ..."
[5]: https://developer.bestbuy.com/apis "Our APIs"
[6]: https://github.com/langchain-ai/langchain?utm_source=chatgpt.com "langchain-ai/langchain: The agent engineering platform."
[7]: https://docs.langchain.com/oss/python/langgraph/workflows-agents "Workflows and agents - Docs by LangChain"
[8]: https://fastapi.tiangolo.com/?utm_source=chatgpt.com "FastAPI - FastAPI"
[9]: https://pydantic.dev/docs/ai/graph/graph/ "Overview | Pydantic Docs"
[10]: https://docs.langchain.com/oss/python/langchain/multi-agent?utm_source=chatgpt.com "Multi-agent - Docs by LangChain"
[11]: https://huggingface.co/docs/datasets/en/loading?utm_source=chatgpt.com "Load"
[12]: https://duckdb.org/docs/current/guides/file_formats/query_parquet?utm_source=chatgpt.com "Querying Parquet Files – DuckDB"
[13]: https://sbert.net/?utm_source=chatgpt.com "SentenceTransformers Documentation — Sentence ..."
[14]: https://github.com/pgvector/pgvector?utm_source=chatgpt.com "pgvector/pgvector: Open-source vector similarity search for ..."
[15]: https://docs.langchain.com/langsmith/evaluation?utm_source=chatgpt.com "LangSmith Evaluation - Docs by LangChain"

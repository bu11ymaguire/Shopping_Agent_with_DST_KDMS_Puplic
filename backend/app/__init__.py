"""SPN Orchestrator / RA-Rec 백엔드.

agent-main의 결정론적 파이프라인을 유지하면서 LLM이 담당하는 구간만 분리한다.

LLM을 쓰는 구간
    SPN Understanding          자유 발화 → 구조화된 상태 갱신 후보
    Latent Hypothesis Generator 관찰 → 숨은 의도 후보
    Response Composer          구조화된 결과 → 자연어

결정론적으로 유지하는 구간
    RA-Rec State Manager       병합, provenance, State Diff, status 전이
    Vagueness / Confidence     수식
    SPN Policy                 분기
    Query Generator            hard constraint → 검색 조건
    Browsing / Ranking         필터링, 리뷰 검색, 점수 계산
    Evaluation                 정답 대비 출력 비교
"""

__all__ = ["__version__"]

__version__ = "0.1.0"

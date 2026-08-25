"""실제 Luxia로 MVP의 6턴 수직 슬라이스를 실행하고 report를 저장한다."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.api import DEMO_SCENARIO  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.service import build_runtime_service  # noqa: E402


async def execute(output: Path, limit: int) -> None:
    service = build_runtime_service()
    snapshot = await service.create_conversation()
    turns = []
    try:
        for utterance in DEMO_SCENARIO[:limit]:
            turn = await service.run_turn(snapshot.conversation_id, utterance)
            turns.append(turn)
            top = turn.rankings[0].product_id if turn.rankings else "clarify"
            composer = turn.trace[-1].output_summary.get("composer", "unknown")
            print(
                f"{turn.turn_id}: {turn.policy.lane} / top={top} / "
                f"composer={composer}"
            )
    finally:
        await service.aclose()

    write_report(
        output,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "conversation_id": snapshot.conversation_id,
            "scenario_turns": limit,
            "turns": [turn.model_dump(mode="json") for turn in turns],
        },
    )
    print(f"report={output.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, choices=range(1, 7), default=6)
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_ROOT / "reports" / "mvp_live_probe.json",
    )
    args = parser.parse_args()
    asyncio.run(execute(args.output, args.turns))


if __name__ == "__main__":
    main()

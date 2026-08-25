"""Run free English turns through Luxia and the installed real tablet catalog."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.actual_service import build_actual_runtime_service  # noqa: E402
from app.llm import write_report  # noqa: E402


DEFAULT_UTTERANCES = [
    "I need a tablet under $300 for note taking with at least 64 GB of storage.",
    "Battery reviews and a good display matter more than having the lightest model.",
    "Show me details for the first result.",
]


async def execute(output: Path, utterances: list[str]) -> None:
    service = build_actual_runtime_service()
    snapshot = await service.create_conversation()
    turns = []
    try:
        for utterance in utterances:
            turn = await service.run_turn(snapshot.conversation_id, utterance)
            turns.append(turn)
            top = turn.rankings[0].product_id if turn.rankings else "clarify"
            composer = turn.trace[-1].output_summary.get("composer", "unknown")
            print(
                f"{turn.turn_id}: {turn.policy.lane} / top={top} / "
                f"products={turn.browse_result.candidate_product_count if turn.browse_result else 0} / "
                f"reviews={turn.browse_result.retrieved_review_count if turn.browse_result else 0} / "
                f"composer={composer}"
            )
    finally:
        await service.aclose()

    write_report(
        output,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "conversation_id": snapshot.conversation_id,
            "catalog_product_count": service.catalog.status().product_count,
            "catalog_review_count": service.catalog.status().review_count,
            "utterances": utterances,
            "turns": [turn.model_dump(mode="json") for turn in turns],
        },
    )
    print(f"report={output.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "utterance",
        nargs="*",
        help="One or more English turns. Defaults to a three-turn smoke probe.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_ROOT / "reports" / "actual_demo_live_probe.json",
    )
    args = parser.parse_args()
    asyncio.run(execute(args.output, args.utterance or DEFAULT_UTTERANCES))


if __name__ == "__main__":
    main()

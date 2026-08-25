"""실제 Luxia에서 첫 Understanding 스키마 요청을 검증한다."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_llm_settings  # noqa: E402
from app.llm import build_client, write_report  # noqa: E402
from app.nodes import understand_utterance  # noqa: E402


async def execute(output: Path) -> None:
    settings = load_llm_settings()
    settings.require_api_key()
    client = build_client(settings, trace=True)
    try:
        result = await understand_utterance(
            client,
            utterance="쓰던 아이폰이 고장 나서 새로 바꿔야 해요.",
            previous_state_summary={},
            conversation_id="schema-probe",
            turn=1,
        )
    finally:
        await client.aclose()

    write_report(
        output,
        {
            "schema_version": result.schema_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "utterance": result.utterance,
            "validated_output": result.model_dump(mode="json"),
        },
    )
    print(f"Understanding 검증 결과 저장: {output.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_ROOT / "reports" / "understanding_probe.json",
    )
    args = parser.parse_args()
    asyncio.run(execute(args.output))


if __name__ == "__main__":
    main()

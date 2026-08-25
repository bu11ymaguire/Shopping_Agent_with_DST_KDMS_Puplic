"""Verify tracked hashes for the untouched tablet-domain holdout freeze."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_multiturn_holdout_v1.json"
)
DATASET_PATH = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
V2_FREEZE = BACKEND_ROOT / "data" / "manifests" / "tablet_domain_v2_freeze.json"
CONTRACT_PATH = BACKEND_ROOT / "app" / "evaluation" / "tablet_domain_holdout.py"
VERIFIER_PATH = BACKEND_ROOT / "scripts" / "verify_tablet_domain_holdout.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_as_crlf_text(path: Path) -> str:
    """Hash text using the CRLF form used when the v2 freeze was recorded."""

    content = path.read_bytes().replace(b"\r\n", b"\n")
    if b"\r" in content:
        raise ValueError(f"unexpected lone CR byte in {path}")
    return hashlib.sha256(content.replace(b"\n", b"\r\n")).hexdigest()


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    check(
        "holdout status is pre-run freeze",
        manifest["status"] == "frozen_before_first_system_run",
    )
    for label, path, key, hash_file in (
        ("dataset", DATASET_PATH, "dataset_sha256", sha256),
        # This one historical hash was frozen from a Windows CRLF checkout.
        ("v2 freeze", V2_FREEZE, "v2_freeze_manifest_sha256", sha256_as_crlf_text),
        ("holdout contract", CONTRACT_PATH, "contract_sha256", sha256),
        ("holdout verifier", VERIFIER_PATH, "verifier_sha256", sha256),
    ):
        check(f"{label} hash", hash_file(path) == manifest[key])
    inputs = manifest["evaluation_input"]
    check("20 frozen scenarios", inputs["scenario_count"] == 20)
    check("81 frozen turns", inputs["turn_count"] == 81)
    check(
        "no product or ranking gold",
        not inputs["predefined_recommendation_products"]
        and not inputs["predefined_rankings"]
        and not inputs["predefined_human_relevance"],
    )
    protocol = manifest["execution_protocol"]
    check(
        "all three conditions use the same frozen input",
        protocol["conditions"] == ["full", "no_memory", "no_review"]
        and protocol["same_input_all_conditions"]
        and protocol["run_each_condition_once"],
    )
    print("Tablet-domain holdout freeze verification completed.")


if __name__ == "__main__":
    main()

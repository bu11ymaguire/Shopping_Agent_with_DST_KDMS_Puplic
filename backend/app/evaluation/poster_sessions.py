"""Split frozen public packets into bounded sessions and merge completed labels."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.poster_annotation import (
    BlindedProductScenario,
    BlindedReviewScenario,
    ProductAnnotationPacket,
    ReviewAnnotationPacket,
)
from app.evaluation.poster_results import load_collection_manifest
from app.llm import write_report


class PosterSessionError(RuntimeError):
    """Raised when annotation sessions would alter or incompletely cover a packet."""


class PosterSessionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductAnnotationSession(PosterSessionContract):
    schema_version: Literal["poster-product-annotation-session-v1"]
    session_id: str = Field(pattern=r"^annotator-\d{2}-product-\d{2}$")
    annotation_kind: Literal["product"]
    annotator_id: str = Field(pattern=r"^annotator-\d{2}$")
    source_packet_id: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_index: int = Field(ge=1)
    session_count: int = Field(ge=1)
    item_count: int = Field(ge=1)
    instructions: list[str]
    scenarios: list[BlindedProductScenario] = Field(min_length=1)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> ProductAnnotationSession:
        count = sum(len(scenario.candidates) for scenario in self.scenarios)
        if count != self.item_count:
            raise ValueError("product session item_count does not match candidates")
        expected = f"{self.annotator_id}-product-{self.session_index:02d}"
        if self.session_id != expected or self.session_index > self.session_count:
            raise ValueError("product session identifier or index is inconsistent")
        return self


class ReviewAnnotationSession(PosterSessionContract):
    schema_version: Literal["poster-review-annotation-session-v1"]
    session_id: str = Field(pattern=r"^annotator-\d{2}-review-\d{2}$")
    annotation_kind: Literal["review"]
    annotator_id: str = Field(pattern=r"^annotator-\d{2}$")
    source_packet_id: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_index: int = Field(ge=1)
    session_count: int = Field(ge=1)
    item_count: int = Field(ge=1)
    instructions: list[str]
    scenarios: list[BlindedReviewScenario] = Field(min_length=1)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> ReviewAnnotationSession:
        count = sum(len(scenario.reviews) for scenario in self.scenarios)
        if count != self.item_count:
            raise ValueError("review session item_count does not match reviews")
        expected = f"{self.annotator_id}-review-{self.session_index:02d}"
        if self.session_id != expected or self.session_index > self.session_count:
            raise ValueError("review session identifier or index is inconsistent")
        return self


class PosterSessionFileRecord(PosterSessionContract):
    relative_path: str = Field(pattern=r"^annotator-\d{2}/sessions/(product|review)-session-\d{2}\.json$")
    annotation_kind: Literal["product", "review"]
    annotator_id: str = Field(pattern=r"^annotator-\d{2}$")
    session_id: str
    session_index: int = Field(ge=1)
    session_count: int = Field(ge=1)
    item_count: int = Field(ge=1)
    immutable_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PosterSessionSetManifest(PosterSessionContract):
    schema_version: Literal["poster-annotation-session-set-v1"]
    packet_base_id: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_items_per_session: int = Field(ge=20, le=200)
    files: list[PosterSessionFileRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def file_sets_are_complete(self) -> PosterSessionSetManifest:
        paths = [item.relative_path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("session relative paths must be unique")
        groups: dict[tuple[str, str], list[PosterSessionFileRecord]] = {}
        for item in self.files:
            groups.setdefault((item.annotator_id, item.annotation_kind), []).append(item)
            if item.item_count > self.max_items_per_session:
                raise ValueError("session exceeds max_items_per_session")
        for key, records in groups.items():
            count = records[0].session_count
            if any(item.session_count != count for item in records):
                raise ValueError(f"session_count differs within {key}")
            if sorted(item.session_index for item in records) != list(
                range(1, count + 1)
            ):
                raise ValueError(f"session indices are not contiguous within {key}")
        return self


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _immutable_session_payload(
    session: ProductAnnotationSession | ReviewAnnotationSession,
) -> dict[str, Any]:
    payload = session.model_dump(mode="json")
    for scenario in payload["scenarios"]:
        items = scenario.get("candidates", scenario.get("reviews", []))
        for item in items:
            item.pop("annotation", None)
    return payload


def _scenario_item_count(
    scenario: BlindedProductScenario | BlindedReviewScenario,
) -> int:
    if isinstance(scenario, BlindedProductScenario):
        return len(scenario.candidates)
    return len(scenario.reviews)


def _partition_scenarios(
    scenarios: list[BlindedProductScenario] | list[BlindedReviewScenario],
    *,
    max_items: int,
) -> list[list[Any]]:
    remaining = list(scenarios)
    if any(_scenario_item_count(item) > max_items for item in remaining):
        raise PosterSessionError("one scenario exceeds max_items_per_session")
    partitions: list[list[Any]] = []
    while remaining:
        remaining_total = sum(_scenario_item_count(item) for item in remaining)
        sessions_left = max(1, math.ceil(remaining_total / max_items))
        target = math.ceil(remaining_total / sessions_left)
        current: list[Any] = []
        current_count = 0
        while remaining:
            next_count = _scenario_item_count(remaining[0])
            if current and current_count + next_count > max_items:
                break
            if current and current_count >= target:
                break
            current.append(remaining.pop(0))
            current_count += next_count
        partitions.append(current)
    return partitions


def _all_annotations_blank(
    packet: ProductAnnotationPacket | ReviewAnnotationPacket,
) -> bool:
    for scenario in packet.scenarios:
        items = (
            scenario.candidates
            if isinstance(scenario, BlindedProductScenario)
            else scenario.reviews
        )
        if any(item.annotation.relevance is not None for item in items):
            return False
    return True


def create_annotation_sessions(
    *,
    collection_dir: Path,
    max_items_per_session: int = 120,
) -> dict[str, Any]:
    if not 20 <= max_items_per_session <= 200:
        raise PosterSessionError("max_items_per_session must be between 20 and 200")
    collection = load_collection_manifest(collection_dir / "collection_manifest.json")
    annotator_ids = sorted(
        set(collection.product_annotator_ids + collection.review_annotator_ids)
    )
    for annotator_id in annotator_ids:
        sessions_dir = collection_dir / annotator_id / "sessions"
        if sessions_dir.exists() and any(sessions_dir.iterdir()):
            raise PosterSessionError(
                f"refusing to overwrite non-empty session directory: {sessions_dir}"
            )

    records: list[PosterSessionFileRecord] = []
    dataset_hash: str | None = None
    for kind, selected_ids, filename, packet_model in (
        (
            "product",
            collection.product_annotator_ids,
            "product_annotations.json",
            ProductAnnotationPacket,
        ),
        (
            "review",
            collection.review_annotator_ids,
            "review_annotations.json",
            ReviewAnnotationPacket,
        ),
    ):
        for annotator_id in selected_ids:
            packet_path = collection_dir / annotator_id / filename
            packet = packet_model.model_validate_json(
                packet_path.read_text(encoding="utf-8")
            )
            if packet.annotator_id != annotator_id:
                raise PosterSessionError(f"packet annotator mismatch: {packet_path}")
            if not _all_annotations_blank(packet):
                raise PosterSessionError(
                    f"session preparation requires a blank packet: {packet_path}"
                )
            if dataset_hash is None:
                dataset_hash = packet.dataset_sha256
            elif packet.dataset_sha256 != dataset_hash:
                raise PosterSessionError("packet dataset hashes differ")
            partitions = _partition_scenarios(
                packet.scenarios,
                max_items=max_items_per_session,
            )
            session_count = len(partitions)
            for index, scenarios in enumerate(partitions, start=1):
                common = {
                    "session_id": f"{annotator_id}-{kind}-{index:02d}",
                    "annotation_kind": kind,
                    "annotator_id": annotator_id,
                    "source_packet_id": packet.packet_id,
                    "dataset_sha256": packet.dataset_sha256,
                    "session_index": index,
                    "session_count": session_count,
                    "item_count": sum(_scenario_item_count(item) for item in scenarios),
                    "instructions": packet.instructions,
                    "scenarios": scenarios,
                }
                if kind == "product":
                    session: ProductAnnotationSession | ReviewAnnotationSession = (
                        ProductAnnotationSession(
                            schema_version="poster-product-annotation-session-v1",
                            **common,
                        )
                    )
                else:
                    session = ReviewAnnotationSession(
                        schema_version="poster-review-annotation-session-v1",
                        **common,
                    )
                relative = (
                    f"{annotator_id}/sessions/{kind}-session-{index:02d}.json"
                )
                path = collection_dir / relative
                write_report(path, session.model_dump(mode="json"))
                records.append(
                    PosterSessionFileRecord(
                        relative_path=relative,
                        annotation_kind=kind,
                        annotator_id=annotator_id,
                        session_id=session.session_id,
                        session_index=index,
                        session_count=session_count,
                        item_count=session.item_count,
                        immutable_payload_sha256=_canonical_sha256(
                            _immutable_session_payload(session)
                        ),
                    )
                )
    if dataset_hash is None:
        raise PosterSessionError("no annotation packets selected")
    manifest = PosterSessionSetManifest(
        schema_version="poster-annotation-session-set-v1",
        packet_base_id=collection.packet_base_id,
        dataset_sha256=dataset_hash,
        max_items_per_session=max_items_per_session,
        files=records,
    )
    manifest_path = collection_dir / "session_manifest.json"
    write_report(manifest_path, manifest.model_dump(mode="json"))
    return {
        "status": "ok",
        "session_manifest": str(manifest_path.resolve()),
        "session_file_count": len(records),
        "max_items_per_session": max_items_per_session,
        "groups": {
            f"{annotator_id}:{kind}": {
                "session_count": len(group),
                "item_counts": [item.item_count for item in group],
                "total_items": sum(item.item_count for item in group),
            }
            for annotator_id in annotator_ids
            for kind in ("product", "review")
            if (
                group := [
                    item
                    for item in records
                    if item.annotator_id == annotator_id
                    and item.annotation_kind == kind
                ]
            )
        },
    }


def _session_items(
    session: ProductAnnotationSession | ReviewAnnotationSession,
) -> list[tuple[str, Any]]:
    output = []
    for scenario in session.scenarios:
        items = (
            scenario.candidates
            if isinstance(scenario, BlindedProductScenario)
            else scenario.reviews
        )
        identifier = "candidate_id" if isinstance(scenario, BlindedProductScenario) else "review_id"
        output.extend(
            (f"{scenario.scenario_id}:{getattr(item, identifier)}", item)
            for item in items
        )
    return output


def _packet_items(
    packet: ProductAnnotationPacket | ReviewAnnotationPacket,
) -> dict[str, Any]:
    output = {}
    for scenario in packet.scenarios:
        items = (
            scenario.candidates
            if isinstance(scenario, BlindedProductScenario)
            else scenario.reviews
        )
        identifier = "candidate_id" if isinstance(scenario, BlindedProductScenario) else "review_id"
        for item in items:
            output[f"{scenario.scenario_id}:{getattr(item, identifier)}"] = item
    return output


def _immutable_item(item: Any) -> dict[str, Any]:
    return item.model_dump(mode="json", exclude={"annotation"})


def merge_completed_sessions(*, collection_dir: Path) -> dict[str, Any]:
    manifest_path = collection_dir / "session_manifest.json"
    manifest = PosterSessionSetManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    collection = load_collection_manifest(collection_dir / "collection_manifest.json")
    if manifest.packet_base_id != collection.packet_base_id:
        raise PosterSessionError("session and collection packet IDs differ")
    merged_files = []
    for kind, selected_ids, filename, packet_model, session_model in (
        (
            "product",
            collection.product_annotator_ids,
            "product_annotations.json",
            ProductAnnotationPacket,
            ProductAnnotationSession,
        ),
        (
            "review",
            collection.review_annotator_ids,
            "review_annotations.json",
            ReviewAnnotationPacket,
            ReviewAnnotationSession,
        ),
    ):
        for annotator_id in selected_ids:
            packet_path = collection_dir / annotator_id / filename
            packet = packet_model.model_validate_json(
                packet_path.read_text(encoding="utf-8")
            )
            source_items = _packet_items(packet)
            completed_items: dict[str, Any] = {}
            records = sorted(
                (
                    item
                    for item in manifest.files
                    if item.annotator_id == annotator_id
                    and item.annotation_kind == kind
                ),
                key=lambda item: item.session_index,
            )
            if not records:
                raise PosterSessionError(f"no {kind} sessions for {annotator_id}")
            for record in records:
                session_path = collection_dir / record.relative_path
                session = session_model.model_validate_json(
                    session_path.read_text(encoding="utf-8")
                )
                if session.source_packet_id != packet.packet_id:
                    raise PosterSessionError(
                        f"session source packet mismatch: {record.relative_path}"
                    )
                immutable_hash = _canonical_sha256(_immutable_session_payload(session))
                if immutable_hash != record.immutable_payload_sha256:
                    raise PosterSessionError(
                        f"session non-annotation content changed: {record.relative_path}"
                    )
                for key, item in _session_items(session):
                    if key in completed_items:
                        raise PosterSessionError(f"duplicate session item: {key}")
                    if item.annotation.relevance is None:
                        raise PosterSessionError(
                            f"incomplete {kind} annotation in {record.relative_path}: {key}"
                        )
                    completed_items[key] = item
            if set(completed_items) != set(source_items):
                missing = sorted(set(source_items) - set(completed_items))
                extra = sorted(set(completed_items) - set(source_items))
                raise PosterSessionError(
                    f"session coverage differs for {annotator_id}/{kind}: "
                    f"missing={missing[:3]}, extra={extra[:3]}"
                )
            for key, completed in completed_items.items():
                if _immutable_item(completed) != _immutable_item(source_items[key]):
                    raise PosterSessionError(f"session item content changed: {key}")

            proposed = packet.model_copy(deep=True)
            proposed_items = _packet_items(proposed)
            for key, completed in completed_items.items():
                proposed_items[key].annotation = completed.annotation.model_copy(deep=True)
            existing_complete = all(
                item.annotation.relevance is not None for item in source_items.values()
            )
            existing_blank = all(
                item.annotation.relevance is None for item in source_items.values()
            )
            if not existing_complete and not existing_blank:
                raise PosterSessionError(f"packet has partially merged labels: {packet_path}")
            if existing_complete and proposed != packet:
                raise PosterSessionError(
                    f"refusing to replace different completed labels: {packet_path}"
                )
            if existing_blank:
                write_report(packet_path, proposed.model_dump(mode="json"))
            merged_files.append(str(packet_path.resolve()))
    return {
        "status": "ok",
        "merged_packet_count": len(merged_files),
        "merged_packets": merged_files,
        "idempotent_if_rerun_with_same_labels": True,
    }

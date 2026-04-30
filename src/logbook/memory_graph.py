from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from logbook.config import AppConfig, MemgraphConfig
from logbook.ledger import MemoryActionReview, RecordingJob, open_ledger, utc_now_iso


PROJECT = "logbook"
SOURCE_PATH_KEYS = {
    "source_path",
    "copied_path",
    "transcript_path",
    "diarization_path",
    "artifact_path",
}


@dataclass(frozen=True)
class MemoryNode:
    id: str
    labels: tuple[str, ...]
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryRelationship:
    id: str
    start_id: str
    type: str
    end_id: str
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryGraphPlan:
    nodes: tuple[MemoryNode, ...]
    relationships: tuple[MemoryRelationship, ...]
    generated_at: str

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def relationship_count(self) -> int:
        return len(self.relationships)

    @property
    def counts_by_label(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self.nodes:
            for label in node.labels:
                counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def counts_by_relationship(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for relationship in self.relationships:
            counts[relationship.type] = counts.get(relationship.type, 0) + 1
        return dict(sorted(counts.items()))


@dataclass(frozen=True)
class MemoryGraphSyncResult:
    plan: MemoryGraphPlan
    execute: bool
    nodes_written: int = 0
    relationships_written: int = 0


class GraphClient(Protocol):
    def run(self, cypher: str, parameters: dict[str, object]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def build_memory_graph_plan(config: AppConfig, job_id: int | None = None) -> MemoryGraphPlan:
    ledger = open_ledger(config.sqlite_path, initialize=True)
    try:
        if job_id is None:
            jobs = ledger.all_jobs()
        else:
            job = ledger.get_by_id(job_id)
            jobs = [job] if job is not None else []
        action_reviews = ledger.memory_action_reviews()
    finally:
        ledger.close()

    builder = _PlanBuilder()
    for job in jobs:
        _add_job(builder, config, job, action_reviews)
    return builder.plan()


def apply_memory_graph_plan(plan: MemoryGraphPlan, client: GraphClient) -> MemoryGraphSyncResult:
    try:
        for labels, nodes in _nodes_by_labels(plan).items():
            client.run(
                _merge_nodes_cypher(labels),
                {
                    "rows": [
                        {
                            "id": node.id,
                            "properties": _safe_properties(node.properties),
                        }
                        for node in nodes
                    ]
                },
            )
        for signature, relationships in _relationships_by_signature(plan).items():
            relationship_type, start_label, end_label = signature
            client.run(
                _merge_relationships_cypher(relationship_type, start_label, end_label),
                {
                    "rows": [
                        {
                            "id": relationship.id,
                            "start_id": relationship.start_id,
                            "end_id": relationship.end_id,
                            "properties": _safe_properties(relationship.properties),
                        }
                        for relationship in relationships
                    ]
                },
            )
    finally:
        client.close()
    return MemoryGraphSyncResult(
        plan=plan,
        execute=True,
        nodes_written=plan.node_count,
        relationships_written=plan.relationship_count,
    )


def query_memory_graph_plan(plan: MemoryGraphPlan, query: str) -> list[dict[str, object]]:
    if query in {"open-loops", "unresolved-actions"}:
        return [
            {
                "id": node.id,
                "text": node.properties.get("text"),
                "job_id": node.properties.get("job_id"),
                "review_status": node.properties.get("review_status"),
                "recorded_at": node.properties.get("recorded_at"),
                "resolved_at": node.properties.get("resolved_at"),
                "resolved_by": node.properties.get("resolved_by"),
            }
            for node in plan.nodes
            if "ActionCandidate" in node.labels
            and node.properties.get("review_status") != "resolved"
        ]
    if query == "recent-decisions":
        return [
            {
                "id": node.id,
                "text": node.properties.get("text"),
                "job_id": node.properties.get("job_id"),
                "recorded_at": node.properties.get("recorded_at"),
            }
            for node in plan.nodes
            if "Decision" in node.labels
        ]
    if query == "topic-trails":
        return [
            {
                "id": node.id,
                "label": _primary_label(node),
                "name": node.properties.get("name"),
            }
            for node in plan.nodes
            if any(label in node.labels for label in ("Topic", "Person", "Project"))
        ]
    if query == "weekly-diff":
        return [
            {
                "id": node.id,
                "label": _primary_label(node),
                "job_id": node.properties.get("job_id"),
                "recorded_at": node.properties.get("recorded_at"),
                "name": node.properties.get("name"),
                "text": node.properties.get("text"),
                "review_status": node.properties.get("review_status"),
                "resolved_at": node.properties.get("resolved_at"),
                "resolved_by": node.properties.get("resolved_by"),
            }
            for node in plan.nodes
            if any(
                label in node.labels
                for label in ("ActionCandidate", "Decision", "Topic", "Person", "Project")
            )
        ]
    raise ValueError(f"unsupported memory graph query: {query}")


class Neo4jMemgraphClient:
    def __init__(self, config: MemgraphConfig) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError as error:
            raise RuntimeError(
                "neo4j package is required for --execute; install project dependencies first"
            ) from error
        auth = None
        if config.username or config.password:
            auth = (config.username or "", config.password or "")
        self._driver = GraphDatabase.driver(
            config.uri,
            auth=auth,
            connection_timeout=5.0,
            keep_alive=False,
            max_connection_lifetime=30.0,
        )
        self._database = config.database
        self._session = self._driver.session(database=self._database)

    def run(self, cypher: str, parameters: dict[str, object]) -> None:
        self._session.run(cypher, parameters).consume()

    def close(self) -> None:
        self._session.close()
        self._driver.close()


class _PlanBuilder:
    def __init__(self) -> None:
        self._nodes: dict[str, MemoryNode] = {}
        self._relationships: dict[str, MemoryRelationship] = {}

    def node(self, node: MemoryNode) -> None:
        existing = self._nodes.get(node.id)
        if existing is None:
            self._nodes[node.id] = _sanitize_node(node)
            return
        labels = tuple(dict.fromkeys(existing.labels + node.labels))
        properties = {**existing.properties, **node.properties}
        self._nodes[node.id] = _sanitize_node(
            MemoryNode(id=node.id, labels=labels, properties=properties)
        )

    def relationship(self, relationship: MemoryRelationship) -> None:
        self._relationships[relationship.id] = _sanitize_relationship(relationship)

    def plan(self) -> MemoryGraphPlan:
        return MemoryGraphPlan(
            nodes=tuple(sorted(self._nodes.values(), key=lambda item: item.id)),
            relationships=tuple(
                sorted(self._relationships.values(), key=lambda item: item.id)
            ),
            generated_at=utc_now_iso(),
        )


def _add_job(
    builder: _PlanBuilder,
    config: AppConfig,
    job: RecordingJob,
    action_reviews: dict[str, MemoryActionReview],
) -> None:
    job_id = _job_node_id(job.id)
    builder.node(
        MemoryNode(
            id=job_id,
            labels=("LogbookJob",),
            properties={
                "project": PROJECT,
                "job_id": job.id,
                "status": job.status,
                "classification": job.classification or "",
                "recorded_at": job.parsed_recorded_at or "",
                "checksum_prefix": job.checksum_sha256[:12],
                "asr_model": job.asr_model or "",
                "diarization_model": job.diarization_model or "",
            },
        )
    )
    _add_generated_note(builder, job, job_id)
    _add_transcript(builder, job, job_id)
    _add_insights(builder, config, job, job_id, action_reviews)


def _add_generated_note(builder: _PlanBuilder, job: RecordingJob, job_id: str) -> None:
    for note_kind, note_path in (
        ("routed_note", job.obsidian_path),
        ("daily_log", job.daily_log_path),
    ):
        if not note_path:
            continue
        note_id = f"{job_id}:note:{_stable_slug(note_kind)}:{_hash_text(note_path)[:10]}"
        builder.node(
            MemoryNode(
                id=note_id,
                labels=("GeneratedNote", "SourceEvidence"),
                properties={
                    "project": PROJECT,
                    "job_id": job.id,
                    "note_kind": note_kind,
                    "note_path": note_path,
                    "artifact_checksum": _hash_text(note_path),
                },
            )
        )
        builder.relationship(
            MemoryRelationship(
                id=f"{job_id}:generated:{note_id}",
                start_id=job_id,
                type="GENERATED",
                end_id=note_id,
            )
        )


def _add_transcript(builder: _PlanBuilder, job: RecordingJob, job_id: str) -> None:
    transcript_value = job.diarization_path or job.transcript_path
    if not transcript_value:
        return
    transcript_path = Path(transcript_value)
    if not transcript_path.exists() or not transcript_path.is_file():
        return
    payload = _read_json(transcript_path)
    artifact_checksum = _file_checksum(transcript_path)
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        segments = [
            {
                "start_seconds": 0.0,
                "end_seconds": 0.0,
                "text": str(payload.get("text") or ""),
                "speaker": None,
            }
        ]
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        segment_id = f"{job_id}:segment:{index:04d}"
        evidence_id = f"{job_id}:evidence:segment:{index:04d}"
        start = float(segment.get("start_seconds") or 0)
        end = float(segment.get("end_seconds") or 0)
        builder.node(
            MemoryNode(
                id=segment_id,
                labels=("TranscriptSegment",),
                properties={
                    "project": PROJECT,
                    "job_id": job.id,
                    "segment_index": index,
                    "start_seconds": start,
                    "end_seconds": end,
                    "speaker": str(segment.get("speaker") or ""),
                    "text": text,
                },
            )
        )
        builder.node(
            MemoryNode(
                id=evidence_id,
                labels=("SourceEvidence",),
                properties={
                    "project": PROJECT,
                    "job_id": job.id,
                    "source_kind": "transcript_segment",
                    "segment_index": index,
                    "start_seconds": start,
                    "end_seconds": end,
                    "recorded_at": job.parsed_recorded_at or "",
                    "artifact_checksum": artifact_checksum,
                },
            )
        )
        builder.relationship(
            MemoryRelationship(
                id=f"{job_id}:has-segment:{index:04d}",
                start_id=job_id,
                type="HAS_SEGMENT",
                end_id=segment_id,
            )
        )
        builder.relationship(
            MemoryRelationship(
                id=f"{segment_id}:supported-by:{evidence_id}",
                start_id=segment_id,
                type="SUPPORTED_BY",
                end_id=evidence_id,
            )
        )
        for topic in _topics_from_text(text):
            _add_topic(builder, topic, evidence_id, job)
        for person in _people_from_text(text):
            _add_person(builder, person, evidence_id, job)
        for project in _projects_from_text(text):
            _add_project(builder, project, evidence_id, job)
        for decision_index, decision in enumerate(_decisions_from_text(text)):
            _add_decision(builder, decision, evidence_id, job, index, decision_index)


def _add_insights(
    builder: _PlanBuilder,
    config: AppConfig,
    job: RecordingJob,
    job_id: str,
    action_reviews: dict[str, MemoryActionReview],
) -> None:
    path = config.processing_root / "insights" / f"job-{job.id:06d}.insights.json"
    if not path.exists():
        return
    payload = _read_json(path)
    artifact_checksum = _file_checksum(path)
    evidence_id = f"{job_id}:evidence:insights"
    builder.node(
        MemoryNode(
            id=evidence_id,
            labels=("SourceEvidence",),
            properties={
                "project": PROJECT,
                "job_id": job.id,
                "source_kind": "insight_artifact",
                "recorded_at": job.parsed_recorded_at or "",
                "artifact_checksum": artifact_checksum,
            },
        )
    )
    raw_actions = payload.get("action_items") or []
    actions = raw_actions if isinstance(raw_actions, list) else []
    for index, action in enumerate(actions):
        text = str(action).strip()
        if not text:
            continue
        action_id = f"{job_id}:action:{index:04d}:{_hash_text(text)[:10]}"
        review = action_reviews.get(action_id)
        review_status = (
            review.review_status
            if review is not None
            else str(payload.get("review_status") or "needs_review")
        )
        builder.node(
            MemoryNode(
                id=action_id,
                labels=("ActionCandidate",),
                properties={
                    "project": PROJECT,
                    "job_id": job.id,
                    "text": text,
                    "review_status": review_status,
                    "canonical": bool(payload.get("canonical", False)),
                    "recorded_at": job.parsed_recorded_at or "",
                    "resolved_at": review.resolved_at if review is not None else "",
                    "resolved_by": review.resolved_by if review is not None else "",
                    "resolution_note": review.resolution_note if review is not None else "",
                },
            )
        )
        builder.relationship(
            MemoryRelationship(
                id=f"{action_id}:supported-by:{evidence_id}",
                start_id=action_id,
                type="SUPPORTED_BY",
                end_id=evidence_id,
            )
        )
        for topic in _topics_from_text(text):
            topic_id = _add_topic(builder, topic, evidence_id, job)
            builder.relationship(
                MemoryRelationship(
                    id=f"{action_id}:relates-to:{topic_id}",
                    start_id=action_id,
                    type="RELATES_TO",
                    end_id=topic_id,
                )
            )
        for person in _people_from_text(text):
            person_id = _add_person(builder, person, evidence_id, job)
            builder.relationship(
                MemoryRelationship(
                    id=f"{action_id}:relates-to:{person_id}",
                    start_id=action_id,
                    type="RELATES_TO",
                    end_id=person_id,
                )
            )
        for project in _projects_from_text(text):
            project_id = _add_project(builder, project, evidence_id, job)
            builder.relationship(
                MemoryRelationship(
                    id=f"{action_id}:relates-to:{project_id}",
                    start_id=action_id,
                    type="RELATES_TO",
                    end_id=project_id,
                )
            )


def _add_topic(
    builder: _PlanBuilder,
    topic: str,
    evidence_id: str,
    job: RecordingJob,
) -> str:
    topic_id = f"{PROJECT}:topic:{_stable_slug(topic)}"
    builder.node(
        MemoryNode(
            id=topic_id,
            labels=("Topic",),
            properties={"project": PROJECT, "name": topic},
        )
    )
    builder.relationship(
        MemoryRelationship(
            id=f"{topic_id}:mentioned-in:{evidence_id}",
            start_id=topic_id,
            type="MENTIONED_IN",
            end_id=evidence_id,
            properties={"job_id": job.id},
        )
    )
    builder.relationship(
        MemoryRelationship(
            id=f"{topic_id}:supported-by:{evidence_id}",
            start_id=topic_id,
            type="SUPPORTED_BY",
            end_id=evidence_id,
            properties={"job_id": job.id},
        )
    )
    return topic_id


def _add_person(
    builder: _PlanBuilder,
    person: str,
    evidence_id: str,
    job: RecordingJob,
) -> str:
    person_id = f"{PROJECT}:person:{_stable_slug(person)}"
    builder.node(
        MemoryNode(
            id=person_id,
            labels=("Person",),
            properties={"project": PROJECT, "name": person},
        )
    )
    builder.relationship(
        MemoryRelationship(
            id=f"{person_id}:mentioned-in:{evidence_id}",
            start_id=person_id,
            type="MENTIONED_IN",
            end_id=evidence_id,
            properties={"job_id": job.id},
        )
    )
    builder.relationship(
        MemoryRelationship(
            id=f"{person_id}:supported-by:{evidence_id}",
            start_id=person_id,
            type="SUPPORTED_BY",
            end_id=evidence_id,
            properties={"job_id": job.id},
        )
    )
    return person_id


def _add_project(
    builder: _PlanBuilder,
    project: str,
    evidence_id: str,
    job: RecordingJob,
) -> str:
    project_id = f"{PROJECT}:project:{_stable_slug(project)}"
    builder.node(
        MemoryNode(
            id=project_id,
            labels=("Project",),
            properties={"project": PROJECT, "name": project},
        )
    )
    builder.relationship(
        MemoryRelationship(
            id=f"{project_id}:mentioned-in:{evidence_id}",
            start_id=project_id,
            type="MENTIONED_IN",
            end_id=evidence_id,
            properties={"job_id": job.id},
        )
    )
    builder.relationship(
        MemoryRelationship(
            id=f"{project_id}:supported-by:{evidence_id}",
            start_id=project_id,
            type="SUPPORTED_BY",
            end_id=evidence_id,
            properties={"job_id": job.id},
        )
    )
    return project_id


def _add_decision(
    builder: _PlanBuilder,
    text: str,
    evidence_id: str,
    job: RecordingJob,
    segment_index: int,
    decision_index: int,
) -> str:
    decision_id = (
        f"{_job_node_id(job.id)}:decision:{segment_index:04d}:"
        f"{decision_index:04d}:{_hash_text(text)[:10]}"
    )
    builder.node(
        MemoryNode(
            id=decision_id,
            labels=("Decision",),
            properties={
                "project": PROJECT,
                "job_id": job.id,
                "text": text,
                "recorded_at": job.parsed_recorded_at or "",
            },
        )
    )
    builder.relationship(
        MemoryRelationship(
            id=f"{decision_id}:supported-by:{evidence_id}",
            start_id=decision_id,
            type="SUPPORTED_BY",
            end_id=evidence_id,
        )
    )
    return decision_id


def _merge_node_cypher(labels: tuple[str, ...]) -> str:
    safe_labels = ":".join(_safe_label(label) for label in labels)
    return f"MERGE (n:{safe_labels} {{id: $id}}) SET n += $properties"


def _merge_nodes_cypher(labels: tuple[str, ...]) -> str:
    safe_labels = ":".join(_safe_label(label) for label in labels)
    return (
        "UNWIND $rows AS row "
        f"MERGE (n:{safe_labels} {{id: row.id}}) "
        "SET n += row.properties"
    )


def _merge_relationship_cypher(relationship_type: str) -> str:
    safe_type = _safe_label(relationship_type)
    return (
        "MATCH (a {id: $start_id}) "
        "MATCH (b {id: $end_id}) "
        f"MERGE (a)-[r:{safe_type} {{id: $id}}]->(b) "
        "SET r += $properties"
    )


def _merge_relationships_cypher(
    relationship_type: str,
    start_label: str | None = None,
    end_label: str | None = None,
) -> str:
    safe_type = _safe_label(relationship_type)
    safe_start = f":{_safe_label(start_label)}" if start_label is not None else ""
    safe_end = f":{_safe_label(end_label)}" if end_label is not None else ""
    return (
        "UNWIND $rows AS row "
        f"MATCH (a{safe_start} {{id: row.start_id}}) "
        f"MATCH (b{safe_end} {{id: row.end_id}}) "
        f"MERGE (a)-[r:{safe_type} {{id: row.id}}]->(b) "
        "SET r += row.properties"
    )


def _nodes_by_labels(plan: MemoryGraphPlan) -> dict[tuple[str, ...], list[MemoryNode]]:
    grouped: dict[tuple[str, ...], list[MemoryNode]] = {}
    for node in plan.nodes:
        grouped.setdefault(node.labels, []).append(node)
    return grouped


def _relationships_by_type(plan: MemoryGraphPlan) -> dict[str, list[MemoryRelationship]]:
    grouped: dict[str, list[MemoryRelationship]] = {}
    for relationship in plan.relationships:
        grouped.setdefault(relationship.type, []).append(relationship)
    return grouped


def _relationships_by_signature(
    plan: MemoryGraphPlan,
) -> dict[tuple[str, str | None, str | None], list[MemoryRelationship]]:
    labels_by_id = {node.id: _primary_label(node) for node in plan.nodes}
    grouped: dict[tuple[str, str | None, str | None], list[MemoryRelationship]] = {}
    for relationship in plan.relationships:
        signature = (
            relationship.type,
            labels_by_id.get(relationship.start_id),
            labels_by_id.get(relationship.end_id),
        )
        grouped.setdefault(signature, []).append(relationship)
    return grouped


def _sanitize_node(node: MemoryNode) -> MemoryNode:
    return MemoryNode(
        id=node.id,
        labels=tuple(_safe_label(label) for label in node.labels),
        properties=_safe_properties(node.properties),
    )


def _sanitize_relationship(relationship: MemoryRelationship) -> MemoryRelationship:
    return MemoryRelationship(
        id=relationship.id,
        start_id=relationship.start_id,
        type=_safe_label(relationship.type),
        end_id=relationship.end_id,
        properties=_safe_properties(relationship.properties),
    )


def _safe_properties(properties: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in properties.items():
        if key in SOURCE_PATH_KEYS:
            continue
        if value is None:
            continue
        if isinstance(value, Path):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
        elif isinstance(value, list):
            safe[key] = [item for item in value if isinstance(item, (str, int, float, bool))]
    return safe


def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", label)
    if not cleaned:
        raise ValueError("empty graph label/type")
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def _primary_label(node: MemoryNode) -> str:
    return next((label for label in node.labels if label != "SourceEvidence"), node.labels[0])


def _job_node_id(job_id: int) -> str:
    return f"{PROJECT}:job:{job_id:06d}"


def _topics_from_text(text: str) -> list[str]:
    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9'-]+", text)
        if len(word) >= 5
    ]
    stop_words = {
        "action",
        "candidate",
        "follow",
        "meeting",
        "remember",
        "transcript",
        "validation",
    }
    topics: list[str] = []
    for word in words:
        if word in stop_words or word in topics:
            continue
        topics.append(word)
        if len(topics) >= 3:
            break
    return topics


def _people_from_text(text: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", text):
        name = match.group(0)
        if name in {"Action", "Follow", "Remember", "Project"}:
            continue
        if name not in names:
            names.append(name)
    return names[:3]


def _projects_from_text(text: str) -> list[str]:
    projects: list[str] = []
    for match in re.finditer(
        r"\bproject\s+([A-Z][A-Za-z0-9_-]{2,})\b",
        text,
        re.IGNORECASE,
    ):
        project = match.group(1)
        if project not in projects:
            projects.append(project)
    return projects[:3]


def _decisions_from_text(text: str) -> list[str]:
    sentences = _sentences(text)
    decision_patterns = (
        re.compile(r"\bdecided\b", re.IGNORECASE),
        re.compile(r"\bdecision\b", re.IGNORECASE),
        re.compile(r"\bagreed\b", re.IGNORECASE),
        re.compile(r"\bwe will\b", re.IGNORECASE),
    )
    return [
        sentence
        for sentence in sentences
        if any(pattern.search(sentence) for pattern in decision_patterns)
    ]


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    return [
        part.strip(" -")
        for part in re.split(r"(?<=[.!?])\s+", normalized)
        if part.strip(" -")
    ]


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"

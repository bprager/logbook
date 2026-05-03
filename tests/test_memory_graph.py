from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from logbook.api import create_app
from logbook.cli import main
from logbook.config import ApiConfig, AppConfig, OdinConfig, RecorderConfig, load_app_config
from logbook.copying import copy_discovered_recordings
from logbook.insights import extract_insights
from logbook.ledger import open_ledger
from logbook.memory_graph import (
    apply_memory_graph_plan,
    apply_memory_graph_repair_plan,
    build_memory_graph_plan,
    build_memory_graph_repair_plan,
    check_memory_graph_health,
    query_memory_graph_plan,
)
from logbook.routing import route_transcripts
from logbook.transcription import transcribe_copied_with_fake_odin


class MemoryGraphTests(TestCase):
    def test_builds_proof_carrying_graph_without_source_audio_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _seed_memory_fixture(root)

            plan = build_memory_graph_plan(app_config, job_id=1)

            self.assertGreater(plan.node_count, 0)
            self.assertGreater(plan.relationship_count, 0)
            self.assertIn("LogbookJob", plan.counts_by_label)
            self.assertIn("TranscriptSegment", plan.counts_by_label)
            self.assertIn("SourceEvidence", plan.counts_by_label)
            self.assertIn("GeneratedNote", plan.counts_by_label)
            self.assertIn("ActionCandidate", plan.counts_by_label)
            self.assertIn("Decision", plan.counts_by_label)
            self.assertIn("Topic", plan.counts_by_label)
            self.assertIn("Person", plan.counts_by_label)
            self.assertIn("Project", plan.counts_by_label)

            supported_types = {"ActionCandidate", "Decision", "Topic", "Person", "Project"}
            for node in plan.nodes:
                if not supported_types.intersection(node.labels):
                    continue
                self.assertTrue(
                    any(
                        relationship.start_id == node.id
                        and relationship.type == "SUPPORTED_BY"
                        for relationship in plan.relationships
                    ),
                    f"{node.id} lacks evidence edge",
                )

            serialized = _serialize_plan(plan)
            self.assertNotIn("source_path", serialized)
            self.assertNotIn("copied_path", serialized)
            self.assertNotIn("transcript_path", serialized)
            self.assertNotIn("diarization_path", serialized)
            self.assertNotIn(str(app_config.processing_root), serialized)
            self.assertNotIn(str(app_config.recorder.mount_path), serialized)
            self.assertNotIn(".mp3", serialized)

            repeated = build_memory_graph_plan(app_config, job_id=1)
            self.assertEqual(
                [node.id for node in plan.nodes],
                [node.id for node in repeated.nodes],
            )
            self.assertEqual(
                [relationship.id for relationship in plan.relationships],
                [relationship.id for relationship in repeated.relationships],
            )

    def test_dry_run_cli_reports_plan_without_memgraph_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            app_config = load_app_config(env_path)
            _seed_memory_fixture_from_config(app_config)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["memory-graph-sync", "--env", str(env_path), "--job-id", "1"])

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Memory graph sync", output)
            self.assertIn("execute=no", output)
            self.assertIn("nodes_written=0", output)
            self.assertIn("relationships_written=0", output)
            self.assertIn("delete_audio=no", output)

    def test_apply_memory_graph_plan_uses_idempotent_merge_cypher(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _seed_memory_fixture(Path(tmp))
            plan = build_memory_graph_plan(app_config, job_id=1)
            client = RecordingGraphClient()

            result = apply_memory_graph_plan(plan, client)

            self.assertTrue(client.closed)
            self.assertTrue(client.runs)
            self.assertEqual(result.nodes_written, plan.node_count)
            self.assertEqual(result.relationships_written, plan.relationship_count)
            self.assertTrue(all("MERGE" in cypher for cypher, _ in client.runs))
            serialized_params = json.dumps([params for _, params in client.runs], sort_keys=True)
            self.assertNotIn(str(app_config.processing_root), serialized_params)
            self.assertNotIn(str(app_config.recorder.mount_path), serialized_params)

    def test_memory_queries_and_api_are_bounded_and_path_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _seed_memory_fixture(root)
            plan = build_memory_graph_plan(app_config, job_id=1)

            open_loops = query_memory_graph_plan(plan, "open-loops")
            decisions = query_memory_graph_plan(plan, "recent-decisions")
            topic_trails = query_memory_graph_plan(plan, "topic-trails")
            weekly_diff = query_memory_graph_plan(plan, "weekly-diff")

            self.assertTrue(any("follow up with Alex" in str(row) for row in open_loops))
            self.assertTrue(any("decided to ship memory graph" in str(row) for row in decisions))
            self.assertTrue(any(row.get("label") == "Project" for row in topic_trails))
            self.assertGreaterEqual(len(weekly_diff), len(open_loops))

            client = TestClient(create_app(app_config))
            api_open_loops = client.get("/memory/open-loops")
            api_decisions = client.get("/memory/recent-decisions")
            api_topics = client.get("/memory/topic-trails")

            self.assertEqual(api_open_loops.status_code, 200)
            self.assertEqual(api_open_loops.json()["query"], "open-loops")
            self.assertGreater(api_open_loops.json()["count"], 0)
            self.assertEqual(api_decisions.status_code, 200)
            self.assertEqual(api_topics.status_code, 200)
            serialized = (
                str(api_open_loops.json())
                + str(api_decisions.json())
                + str(api_topics.json())
            )
            self.assertNotIn(str(app_config.processing_root), serialized)
            self.assertNotIn(str(app_config.recorder.mount_path), serialized)
            self.assertNotIn(".mp3", serialized)

    def test_memory_graph_health_reports_ok_for_matching_live_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _seed_memory_fixture(Path(tmp))
            plan = build_memory_graph_plan(app_config)
            client = HealthGraphClient(
                live_nodes=plan.node_count,
                live_relationships=plan.relationship_count,
                label_counts=plan.counts_by_label,
                relationship_counts=plan.counts_by_relationship,
            )

            health = check_memory_graph_health(app_config, client=client)

            self.assertEqual(health.status, "ok")
            self.assertTrue(health.reachable)
            self.assertEqual(health.live_nodes, plan.node_count)
            self.assertEqual(health.live_relationships, plan.relationship_count)
            self.assertTrue(all(value == 0 for value in health.drift_by_label.values()))
            self.assertTrue(
                all(value == 0 for value in health.drift_by_relationship.values())
            )

    def test_memory_graph_health_reports_drift_for_mismatched_live_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _seed_memory_fixture(Path(tmp))
            plan = build_memory_graph_plan(app_config)
            relationship_counts = dict(plan.counts_by_relationship)
            relationship_counts["SUPPORTED_BY"] -= 1
            client = HealthGraphClient(
                live_nodes=plan.node_count,
                live_relationships=plan.relationship_count - 1,
                label_counts=plan.counts_by_label,
                relationship_counts=relationship_counts,
            )

            health = check_memory_graph_health(app_config, client=client)

            self.assertEqual(health.status, "drift")
            self.assertTrue(health.reachable)
            self.assertEqual(health.drift_by_relationship["SUPPORTED_BY"], -1)

    def test_memory_graph_health_is_not_configured_without_memgraph(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _seed_memory_fixture(Path(tmp))
            client = TestClient(create_app(app_config))

            health = check_memory_graph_health(app_config)
            response = client.get("/memory/graph-health")

            self.assertEqual(health.status, "not_configured")
            self.assertFalse(health.reachable)
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "not_configured")
            self.assertIn("planned_nodes", payload)
            self.assertEqual(payload["live_nodes"], None)

    def test_memory_graph_repair_plan_reports_missing_and_stale_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _seed_memory_fixture(Path(tmp))
            plan = build_memory_graph_plan(app_config)
            missing_node = next(node for node in plan.nodes if "GeneratedNote" in node.labels)
            missing_relationship = next(
                relationship
                for relationship in plan.relationships
                if relationship.type == "SUPPORTED_BY"
            )
            stale_node_id = "logbook:job:999999:note:routed-note:stale"
            stale_relationship_id = "logbook:job:999999:generated:stale"
            stale_evidence_relationship_id = (
                "logbook:person:stale:supported-by:"
                "logbook:job:999999:evidence:segment:0000"
            )
            client = RepairGraphClient(
                live_node_ids=[
                    node.id for node in plan.nodes if node.id != missing_node.id
                ]
                + [stale_node_id],
                live_relationship_ids=[
                    relationship.id
                    for relationship in plan.relationships
                    if relationship.id != missing_relationship.id
                ]
                + [stale_relationship_id, stale_evidence_relationship_id],
            )

            repair_plan = build_memory_graph_repair_plan(plan, client)

            self.assertEqual([node.id for node in repair_plan.missing_nodes], [missing_node.id])
            self.assertEqual(repair_plan.stale_node_ids, (stale_node_id,))
            self.assertEqual(
                [relationship.id for relationship in repair_plan.missing_relationships],
                [missing_relationship.id],
            )
            self.assertEqual(
                repair_plan.stale_relationship_ids,
                (stale_relationship_id, stale_evidence_relationship_id),
            )
            self.assertTrue(client.closed)

    def test_apply_memory_graph_repair_plan_upserts_missing_and_prunes_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            app_config = _seed_memory_fixture(Path(tmp))
            plan = build_memory_graph_plan(app_config)
            missing_node = next(node for node in plan.nodes if "GeneratedNote" in node.labels)
            missing_relationship = next(
                relationship
                for relationship in plan.relationships
                if relationship.type == "SUPPORTED_BY"
            )
            stale_node_id = "logbook:job:999999:note:routed-note:stale"
            stale_relationship_id = "logbook:job:999999:generated:stale"
            stale_evidence_relationship_id = (
                "logbook:person:stale:supported-by:"
                "logbook:job:999999:evidence:segment:0000"
            )
            client = RepairGraphClient(
                live_node_ids=[
                    node.id for node in plan.nodes if node.id != missing_node.id
                ]
                + [stale_node_id],
                live_relationship_ids=[
                    relationship.id
                    for relationship in plan.relationships
                    if relationship.id != missing_relationship.id
                ]
                + [stale_relationship_id, stale_evidence_relationship_id],
            )
            repair_plan = build_memory_graph_repair_plan(plan, client)

            result = apply_memory_graph_repair_plan(
                repair_plan,
                client,
                prune_stale=True,
            )

            self.assertEqual(result.nodes_written, 1)
            self.assertEqual(result.relationships_written, 1)
            self.assertEqual(result.relationships_pruned, 2)
            self.assertEqual(result.nodes_pruned, 1)
            self.assertTrue(
                any(
                    params.get("ids")
                    == [stale_relationship_id, stale_evidence_relationship_id]
                    for _, params in client.runs
                )
            )
            self.assertTrue(any(params.get("ids") == [stale_node_id] for _, params in client.runs))
            self.assertTrue(client.closed)

    def test_memory_graph_repair_cli_is_dry_run_first(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            app_config = load_app_config(env_path)
            _seed_memory_fixture_from_config(app_config)
            plan = build_memory_graph_plan(app_config)
            stale_node_id = "logbook:job:999999:note:routed-note:stale"
            stale_relationship_id = "logbook:job:999999:generated:stale"
            client = RepairGraphClient(
                live_node_ids=[node.id for node in plan.nodes] + [stale_node_id],
                live_relationship_ids=[
                    relationship.id for relationship in plan.relationships
                ]
                + [stale_relationship_id],
            )
            stdout = io.StringIO()

            with patch("logbook.cli.Neo4jMemgraphClient", return_value=client):
                with redirect_stdout(stdout):
                    exit_code = main(["memory-graph-repair", "--env", str(env_path)])

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Memory graph repair", output)
            self.assertIn("execute=no", output)
            self.assertIn("prune_stale=no", output)
            self.assertIn("stale_nodes=1", output)
            self.assertIn("stale_relationships=1", output)
            self.assertIn("nodes_written=0", output)
            self.assertIn("relationships_written=0", output)
            self.assertFalse(client.runs)

    def test_memory_action_resolution_removes_action_from_open_loops(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _seed_memory_fixture(root)
            plan = build_memory_graph_plan(app_config, job_id=1)
            action = next(
                row
                for row in query_memory_graph_plan(plan, "open-loops")
                if "follow up with Alex" in str(row.get("text"))
            )
            action_id = str(action["id"])
            api_config = replace(
                app_config,
                api=ApiConfig(
                    bind_host="127.0.0.1",
                    port=8787,
                    action_token="action-secret",
                ),
            )
            client = TestClient(create_app(api_config))

            response = client.post(
                f"/memory/actions/{action_id}/resolve",
                json={
                    "reason": "Handled in inbox zero pass.",
                    "requested_by": "openclaw.test",
                    "idempotency_key": "resolve-once",
                },
                headers={"Authorization": "Bearer action-secret"},
            )

            self.assertEqual(response.status_code, 202)
            payload = response.json()
            self.assertEqual(payload["action_id"], action_id)
            self.assertEqual(payload["review_status"], "resolved")
            self.assertEqual(payload["resolved_by"], "openclaw.test")
            self.assertEqual(payload["resolution_note"], "Handled in inbox zero pass.")
            resolved_plan = build_memory_graph_plan(app_config, job_id=1)
            resolved_open_loops = query_memory_graph_plan(resolved_plan, "open-loops")
            self.assertFalse(any(row["id"] == action_id for row in resolved_open_loops))
            resolved_node = next(node for node in resolved_plan.nodes if node.id == action_id)
            self.assertEqual(resolved_node.properties["review_status"], "resolved")
            self.assertEqual(resolved_node.properties["resolved_by"], "openclaw.test")

            ledger = open_ledger(app_config.sqlite_path)
            try:
                reviews = ledger.memory_action_reviews()
                audits = ledger.connection.execute(
                    """
                    SELECT action_type, target_type, target_id, requested_by
                    FROM action_audit
                    WHERE action_type = 'memory.action.resolve'
                    """
                ).fetchall()
            finally:
                ledger.close()

            self.assertIn(action_id, reviews)
            self.assertEqual(reviews[action_id].review_status, "resolved")
            self.assertEqual(len(audits), 1)
            self.assertEqual(audits[0]["target_id"], action_id)

    def test_memory_action_resolve_cli_is_dry_run_first(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = _write_env(root)
            app_config = load_app_config(env_path)
            _seed_memory_fixture_from_config(app_config)
            action_id = str(
                next(
                    row
                    for row in query_memory_graph_plan(
                        build_memory_graph_plan(app_config),
                        "open-loops",
                    )
                    if "follow up with Alex" in str(row.get("text"))
                )["id"]
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "memory-action-resolve",
                        "--env",
                        str(env_path),
                        "--action-id",
                        action_id,
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("Memory action resolve", output)
            self.assertIn("execute=no", output)
            self.assertIn("review_status=would_resolve", output)
            ledger = open_ledger(app_config.sqlite_path)
            try:
                reviews = ledger.memory_action_reviews()
            finally:
                ledger.close()
            self.assertNotIn(action_id, reviews)


class RecordingGraphClient:
    def __init__(self) -> None:
        self.runs: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def run(self, cypher: str, parameters: dict[str, object]) -> None:
        self.runs.append((cypher, parameters))

    def close(self) -> None:
        self.closed = True


class HealthGraphClient:
    def __init__(
        self,
        live_nodes: int,
        live_relationships: int,
        label_counts: dict[str, int],
        relationship_counts: dict[str, int],
    ) -> None:
        self.live_nodes = live_nodes
        self.live_relationships = live_relationships
        self.label_counts = label_counts
        self.relationship_counts = relationship_counts

    def query(
        self,
        cypher: str,
        parameters: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        del parameters
        if "UNWIND labels(n) AS label" in cypher:
            return [
                {"label": label, "count": count}
                for label, count in self.label_counts.items()
            ]
        if "RETURN type(r) AS type" in cypher:
            return [
                {"type": relationship_type, "count": count}
                for relationship_type, count in self.relationship_counts.items()
            ]
        if "MATCH (a)-[r]->(b)" in cypher:
            return [{"count": self.live_relationships}]
        return [{"count": self.live_nodes}]

    def close(self) -> None:
        pass


class RepairGraphClient:
    def __init__(
        self,
        live_node_ids: list[str],
        live_relationship_ids: list[str],
    ) -> None:
        self.live_node_ids = live_node_ids
        self.live_relationship_ids = live_relationship_ids
        self.runs: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def query(
        self,
        cypher: str,
        parameters: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        del parameters
        if "RETURN r.id AS id" in cypher:
            return [{"id": relationship_id} for relationship_id in self.live_relationship_ids]
        return [{"id": node_id} for node_id in self.live_node_ids]

    def run(self, cypher: str, parameters: dict[str, object]) -> None:
        self.runs.append((cypher, parameters))

    def close(self) -> None:
        self.closed = True


def _seed_memory_fixture(root: Path) -> AppConfig:
    app_config = _app_config(root)
    _seed_memory_fixture_from_config(app_config)
    return app_config


def _seed_memory_fixture_from_config(app_config: AppConfig) -> None:
    _write_recording(app_config.recorder.recordings_dir / "260429_1000.mp3")
    copy_discovered_recordings(app_config)
    transcribe_copied_with_fake_odin(app_config)
    _rewrite_transcript(
        app_config.sqlite_path,
        1,
        (
            "task Project Atlas sync. Action item follow up with Alex. "
            "We decided to ship memory graph."
        ),
    )
    vault_root = app_config.processing_root.parent / "test-vault"
    route_transcripts(app_config, vault_root)
    extract_insights(app_config, vault_root, job_id=1)


def _app_config(root: Path) -> AppConfig:
    mount = root / "IC RECORDER"
    recordings_dir = mount / "REC_FILE" / "FOLDER01"
    recordings_dir.mkdir(parents=True)
    return AppConfig(
        processing_root=root / "VoiceIngest",
        sqlite_path=root / "VoiceIngest" / "voice_ingest.sqlite",
        recorder=RecorderConfig(
            volume_name="IC RECORDER",
            mount_path=mount,
            recordings_path="/REC_FILE/FOLDER01",
        ),
        odin=_odin_config(),
    )


def _write_env(root: Path) -> Path:
    mount = root / "IC RECORDER"
    recordings_dir = mount / "REC_FILE" / "FOLDER01"
    recordings_dir.mkdir(parents=True)
    env_path = root / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"LOGBOOK_PROCESSING_ROOT={root / 'VoiceIngest'}",
                "SONY_RECORDER_VOLUME_NAME=IC RECORDER",
                f"SONY_RECORDER_MOUNT_PATH={mount}",
                "SONY_RECORDER_RECORDINGS_PATH=/REC_FILE/FOLDER01",
                "ODIN_API_BASE_URL=http://odin.test",
                "MEMGRAPH_URI=bolt://odin:7697",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return env_path


def _write_recording(path: Path) -> None:
    path.write_bytes(f"fake mp3 bytes {path.name}".encode("utf-8"))
    timestamp = datetime(2026, 4, 29, 10, 0, 54).timestamp()
    os.utime(path, (timestamp, timestamp))


def _rewrite_transcript(sqlite_path: Path, job_id: int, text: str) -> None:
    ledger = open_ledger(sqlite_path)
    try:
        job = ledger.get_by_id(job_id)
    finally:
        ledger.close()
    assert job is not None
    assert job.transcript_path is not None
    path = Path(job.transcript_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["text"] = text
    payload["segments"] = [
        {
            "start_seconds": 0.0,
            "end_seconds": 2.0,
            "speaker": None,
            "text": text,
        }
    ]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _odin_config() -> OdinConfig:
    return OdinConfig(
        api_base_url="http://odin.test",
        api_token=None,
        asr_model="large-v3",
        asr_device="cuda",
        asr_compute_type="float16",
        asr_vad_filter=True,
        diarization_model="pyannote/speaker-diarization-3.1",
    )


def _serialize_plan(plan) -> str:
    return json.dumps(
        {
            "nodes": [
                {
                    "id": node.id,
                    "labels": node.labels,
                    "properties": node.properties,
                }
                for node in plan.nodes
            ],
            "relationships": [
                {
                    "id": relationship.id,
                    "type": relationship.type,
                    "start_id": relationship.start_id,
                    "end_id": relationship.end_id,
                    "properties": relationship.properties,
                }
                for relationship in plan.relationships
            ],
        },
        sort_keys=True,
    )

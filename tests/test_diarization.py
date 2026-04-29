from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from logbook.config import AppConfig, OdinConfig, RecorderConfig
from logbook.copying import copy_discovered_recordings
from logbook.diarization import diarize_meetings, diarize_meetings_with_fake_odin
from logbook.ledger import open_ledger
from logbook.odin import OdinSubmitRequest, OdinSubmitResponse, OdinTranscriptResult
from logbook.transcription import transcribe_copied_with_fake_odin


class DiarizationTests(TestCase):
    def test_diarizes_only_meeting_transcripts_and_records_speakers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_result = transcribe_copied_with_fake_odin(app_config)
            transcript_path = transcribe_result.items[0].transcript_path
            self.assertIsNotNone(transcript_path)
            _replace_transcript_text(transcript_path, "Meeting weekly planning.")

            result = diarize_meetings_with_fake_odin(app_config)

            self.assertEqual(result.diarized_count, 1)
            self.assertEqual(result.failed_count, 0)
            self.assertEqual(result.items[0].speaker_labels, ("SPEAKER_00", "SPEAKER_01"))
            diarization_path = result.items[0].diarization_path
            self.assertIsNotNone(diarization_path)
            payload = json.loads(diarization_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["diarization_model"], "pyannote/speaker-diarization-3.1")
            self.assertEqual(
                {segment["speaker"] for segment in payload["segments"]},
                {"SPEAKER_00", "SPEAKER_01"},
            )

            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(result.items[0].job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "diarized")
            self.assertEqual(job.diarization_path, str(diarization_path))
            self.assertEqual(job.diarization_model, "pyannote/speaker-diarization-3.1")
            self.assertIsNotNone(job.diarized_at)

    def test_skips_non_meeting_transcripts_without_mutating_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_result = transcribe_copied_with_fake_odin(app_config)

            result = diarize_meetings_with_fake_odin(app_config)

            self.assertEqual(result.diarized_count, 0)
            self.assertEqual(result.skipped_count, 1)
            self.assertEqual(result.items[0].status, "skipped_not_meeting")
            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(transcribe_result.items[0].job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "transcribed")
            self.assertIsNone(job.diarization_path)

    def test_missing_speaker_labels_fail_without_mutating_job(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = _app_config(root)
            _write_recording(app_config.recorder.recordings_dir / "260429_0821.mp3")
            copy_discovered_recordings(app_config)
            transcribe_result = transcribe_copied_with_fake_odin(app_config)
            transcript_path = transcribe_result.items[0].transcript_path
            self.assertIsNotNone(transcript_path)
            _replace_transcript_text(transcript_path, "Meeting weekly planning.")

            result = diarize_meetings(app_config, client=NoSpeakerOdinClient(app_config.odin))

            self.assertEqual(result.failed_count, 1)
            self.assertEqual(result.items[0].status, "failed_missing_speaker_labels")
            self.assertEqual(list((app_config.processing_root / "diarization").glob("*.json")), [])
            ledger = open_ledger(app_config.sqlite_path)
            try:
                job = ledger.get_by_checksum(transcribe_result.items[0].job.checksum_sha256)
            finally:
                ledger.close()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "transcribed")
            self.assertIsNone(job.diarization_path)


class NoSpeakerOdinClient:
    def __init__(self, config: OdinConfig) -> None:
        self.config = config

    def submit_transcription(self, submit_request: OdinSubmitRequest) -> OdinSubmitResponse:
        return OdinSubmitResponse(odin_job_id=f"nospeaker-{submit_request.job_id}", status="succeeded")

    def get_result(self, odin_job_id: str) -> OdinTranscriptResult:
        return OdinTranscriptResult(
            odin_job_id=odin_job_id,
            status="succeeded",
            text="meeting weekly planning",
            language="en",
            asr_model=f"fake-{self.config.asr_model}",
            diarization_model=self.config.diarization_model,
            segments=(),
        )


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


def _write_recording(path: Path) -> None:
    path.write_bytes(b"fake mp3 bytes")
    timestamp = datetime(2026, 4, 29, 8, 21, 54).timestamp()
    os.utime(path, (timestamp, timestamp))


def _replace_transcript_text(path: Path, text: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["text"] = text
    path.write_text(json.dumps(payload), encoding="utf-8")


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

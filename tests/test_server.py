import http.client
import json
import threading
from dataclasses import replace
from hashlib import sha256

import pytest

from mentor.chat_service import Answer, StreamEvent
from mentor.anchors import SourceAnchor
from mentor.compilation import CompilationRun, CorpusSnapshot, SourceProcessingResult
from mentor.derived_records import Claim, RecordDependency
from mentor.knowledge import Collection, Source as LibrarySource, SourceRevision
from mentor.server import create_server
from mentor.storage import SourceChange, Storage


class FakeChatService:
    def reply(self, thread_id, question):
        return Answer(text=f"Answer: {question}", citations=[], evidence=[])


class StreamingFakeChatService:
    def __init__(self):
        self.evaluation = None

    def stream_reply(self, thread_id, question, evaluation):
        self.evaluation = evaluation
        yield StreamEvent("delta", "A")
        yield StreamEvent("complete", answer=Answer(text="A", citations=[], evidence=[]))


class FailingStreamingFakeChatService:
    def stream_reply(self, thread_id, question, evaluation):
        yield StreamEvent("error", error="The mentor request failed. Try again.")


class ExplodingStreamingFakeChatService:
    def stream_reply(self, thread_id, question, evaluation):
        raise RuntimeError("simulated server failure")


def request(server, method, path, body=None):
    connection = http.client.HTTPConnection(*server.server_address)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    result = (response.status, dict(response.getheaders()), response.read())
    connection.close()
    return result


def inspected_snapshot(storage, *, run_id="run_inspector", publish=True):
    transcript = "[00:00:01] PRIVATE RAW TRANSCRIPT BODY must never be returned."
    collection = Collection("collection_inspector", "Inspector tests", "trading", True, "test")
    source = LibrarySource.create(
        collection_id=collection.collection_id,
        identity_key=f"identity-{run_id}",
        source_type="transcript",
        author="Synthetic Author",
        course="Synthetic Course",
        lesson_title="Synthetic lesson",
        year=2026,
        original_filename="synthetic.txt",
        local_provenance="C:/private/synthetic.txt",
    )
    revision = SourceRevision.create(
        source=source,
        content_sha256=sha256(transcript.encode()).hexdigest(),
        byte_size=len(transcript.encode()),
        local_locator="C:/private/synthetic.txt",
        observed_at=1.0,
        lifecycle_state="active",
        remote_file_id="file_nonpublic",
    )
    storage.store_collection(collection)
    storage.store_source(source)
    storage.store_source_revision(revision)
    run = CompilationRun(run_id, "synthetic-model", "synthetic-prompt", "synthetic-schema", 1.0)
    snapshot = CorpusSnapshot.create(
        run=run,
        selected_revisions=(revision,),
        raw_store_id=None,
        derived_store_id=None,
        created_at=1.0,
    )
    storage.create_compilation_candidate(run, snapshot)
    anchor = SourceAnchor.create(
        revision=revision,
        transcript=transcript,
        start_offset=0,
        end_offset=len(transcript),
    )
    storage.store_source_anchors((anchor,))
    with pytest.raises(ValueError, match="identity is not canonical"):
        storage.store_source_anchors((replace(anchor, anchor_id="anc_not_canonical"),))
    record = Claim.create(
        snapshot_id=snapshot.snapshot_id,
        anchors=(anchor.anchor_id,),
        dependencies=(RecordDependency("source_revision", revision.revision_id),),
        validation_state="validated",
        lifecycle_state="active",
        qualification="Synthetic inspector evidence.",
        subject="Synthetic subject",
        predicate="supports",
        object="Synthetic object",
    )
    storage.store_derived_record(record)
    storage.record_candidate_gate(
        snapshot.snapshot_id,
        (SourceProcessingResult(revision.revision_id, "processed", 1),),
        checked_at=2.0,
    )
    if publish:
        storage.record_candidate_store(snapshot.snapshot_id, "raw", "vs_private_raw")
        storage.record_candidate_store(snapshot.snapshot_id, "derived", "vs_private_derived")
        storage.transition_snapshot(snapshot.snapshot_id, "validating", transitioned_at=2.0)
        snapshot = storage.transition_snapshot(snapshot.snapshot_id, "published", transitioned_at=3.0)
    return snapshot, record, anchor, source


def test_server_exposes_read_only_inspector_without_raw_or_private_payloads(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    snapshot, record, anchor, source = inspected_snapshot(storage)
    storage.store_source_change(
        SourceChange(source.source_id, "changed", None, "C:/private/synthetic.txt", 4.0)
    )
    failed_snapshot, _failed_record, _failed_anchor, _failed_source = inspected_snapshot(
        storage, run_id="run_failed", publish=False
    )
    storage.transition_snapshot(failed_snapshot.snapshot_id, "validating", transitioned_at=4.0)
    storage.transition_snapshot(
        failed_snapshot.snapshot_id, "failed", transitioned_at=5.0, failure_reason="Synthetic failure."
    )
    thread_id = storage.create_thread("Inspector turn")
    storage.record_display_turn(
        thread_id,
        user_text="Private question",
        answer_markdown="Private answer",
        citations=[],
        evidence=[],
        diagnostics={
            "knowledge_context": {
                "status": "used",
                "used": True,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_schema_version": "synthetic-schema",
                "record_ids": [record.record_id],
                "record_count": 1,
                "budget": {"max_records": 4, "max_tokens": 300},
                "used_tokens": 42,
                "truncated": False,
            },
            "private_reasoning": "must never be returned",
        },
        response_id="resp_inspector",
        status="completed",
        incomplete_reason=None,
    )
    storage.record_display_turn(
        thread_id,
        user_text="Another private question",
        answer_markdown="Another private answer",
        citations=[],
        evidence=[],
        diagnostics={
            "knowledge_context": {
                "status": "used",
                "used": True,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_schema_version": "synthetic-schema",
                "record_ids": ["private reasoning must not become an audit ID"],
                "budget": {"max_records": 4, "max_tokens": 300},
                "used_tokens": 42,
                "truncated": False,
            },
        },
        response_id="resp_private",
        status="completed",
        incomplete_reason=None,
    )
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _headers, body = request(server, "GET", "/api/knowledge")
        payload = json.loads(body)
        assert status == 200
        assert payload["current_snapshot"]["snapshot_id"] == snapshot.snapshot_id
        assert {item["status"] for item in payload["snapshots"]} == {"published", "failed"}
        assert payload["pending_source_changes"] == [{"source_id": source.source_id, "lifecycle_state": "changed", "revision_id": None, "observed_at": 4.0}]
        assert b"PRIVATE RAW TRANSCRIPT BODY" not in body
        assert b"C:/private" not in body
        assert b"file_nonpublic" not in body
        assert b"vs_private" not in body

        status, _headers, body = request(server, "GET", f"/api/knowledge/snapshots/{snapshot.snapshot_id}")
        detail = json.loads(body)
        assert status == 200
        assert detail["coverage"] == {"processed": 1, "failed": 0}
        assert detail["records"] == [{
            "record_id": record.record_id,
            "family": "claim",
            "derived_kind": "statement",
            "evidence_state": "raw_taught",
            "validation_state": "validated",
            "lifecycle_state": "active",
            "provenance_label": "derived; independently validated against raw anchors",
            "stale": False,
        }]
        assert detail["compiler"] == {
            "model_version": "synthetic-model",
            "prompt_version": "synthetic-prompt",
            "schema_version": "synthetic-schema",
        }

        status, _headers, body = request(
            server, "GET", f"/api/knowledge/snapshots/{snapshot.snapshot_id}/records/{record.record_id}"
        )
        record_payload = json.loads(body)
        assert status == 200
        assert record_payload["anchors"] == [{
            "anchor_id": anchor.anchor_id,
            "collection_id": anchor.collection_id,
            "source_id": anchor.source_id,
            "revision_id": anchor.revision_id,
            "revision_sha256": anchor.revision_sha256,
            "filename": "synthetic.txt",
            "lesson_title": "Synthetic lesson",
            "author": "Synthetic Author",
            "course": "Synthetic Course",
            "year": 2026,
            "timestamp_start_ms": 1000,
            "timestamp_end_ms": 1000,
            "start_offset": 0,
            "end_offset": len(transcript := "[00:00:01] PRIVATE RAW TRANSCRIPT BODY must never be returned."),
            "span_fingerprint": anchor.span_fingerprint,
            "locator_version": "transcript-v1",
        }]
        assert record_payload["dependencies"] == [{"kind": "source_revision", "identifier": anchor.revision_id}]
        assert b"PRIVATE RAW TRANSCRIPT BODY" not in body

        status, _headers, body = request(server, "GET", f"/api/knowledge/threads/{thread_id}/orientation")
        assert status == 200
        assert json.loads(body) == {"thread_id": thread_id, "turns": [{
            "turn_number": 1,
            "response_id": "resp_inspector",
            "knowledge_context": {
                "status": "used",
                "used": True,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_schema_version": "synthetic-schema",
                "record_ids": [record.record_id],
                "record_count": 1,
                "budget": {"max_records": 4, "max_tokens": 300},
                "used_tokens": 42,
                "truncated": False,
            },
        }]}
        assert b"private_reasoning" not in body
        assert b"Private question" not in body
        assert b"Private answer" not in body

        assert request(server, "GET", "/api/knowledge/snapshots/missing")[0] == 404
        assert request(server, "GET", f"/api/knowledge/snapshots/{snapshot.snapshot_id}/records/missing")[0] == 404
        assert request(server, "GET", "/api/knowledge/threads/999/orientation")[0] == 404
        assert request(server, "POST", "/api/knowledge", b"{}")[0] == 404
        assert request(server, "DELETE", "/api/knowledge")[0] == 404
    finally:
        server.shutdown()
        worker.join()


def test_server_inspector_stays_loopback_only(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    server = create_server(storage, FakeChatService(), port=0)
    assert server.server_address[0] == "127.0.0.1"


def test_server_binds_loopback_and_only_serves_registered_sources(tmp_path):
    transcript = tmp_path / "lesson.txt"
    transcript.write_text("original source", encoding="utf-8")
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.register_source(
        relative_path="lesson.txt",
        filename="lesson.txt",
        year=2026,
        local_path=str(transcript),
        modified_at=transcript.stat().st_mtime,
        file_id="file_allowed",
        vector_store_file_id="vsf_allowed",
    )
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        assert server.server_address[0] == "127.0.0.1"
        status, headers, body = request(server, "GET", "/api/sources/file_allowed")
        assert status == 200
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert body == b"original source"
        assert request(server, "GET", "/api/sources/not_registered")[0] == 404
    finally:
        server.shutdown()
        worker.join()


def test_server_creates_thread_and_validates_message_json(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _, body = request(server, "POST", "/api/threads", b'{"title":"Plan"}')
        assert status == 201
        assert b'"title": "Plan"' in body
        assert request(server, "POST", "/api/threads", b'{"title":"   "}')[0] == 400
        assert request(server, "POST", "/api/threads/99/messages", b'{"question":"Hello"}')[0] == 404
        assert request(server, "POST", "/api/threads/1/messages", b'not json')[0] == 400
    finally:
        server.shutdown()
        worker.join()


def test_server_streams_chat_events(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Plan")
    chat_service = StreamingFakeChatService()
    server = create_server(storage, chat_service, port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, headers, body = request(
            server,
            "POST",
            f"/api/threads/{thread_id}/messages",
            b'{"question":"Hello","evaluation":{"reasoning_effort":"xhigh","reasoning_mode":"pro","research_depth":"deep"}}',
        )
        assert status == 200
        assert headers["Content-Type"] == "text/event-stream; charset=utf-8"
        assert b'"type": "delta"' in body
        assert b'"type": "complete"' in body
        assert chat_service.evaluation.reasoning_effort == "xhigh"
        assert chat_service.evaluation.reasoning_mode == "pro"
        assert chat_service.evaluation.research_depth == "deep"
    finally:
        server.shutdown()
        worker.join()


def test_server_streams_a_recoverable_error_event(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Plan")
    server = create_server(storage, FailingStreamingFakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _, body = request(server, "POST", f"/api/threads/{thread_id}/messages", b'{"question":"Hello"}')
        assert status == 200
        assert b'"type": "error"' in body
        assert b"The mentor request failed. Try again." in body
    finally:
        server.shutdown()
        worker.join()


def test_server_recovers_an_unexpected_streaming_exception(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Plan")
    server = create_server(storage, ExplodingStreamingFakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _, body = request(server, "POST", f"/api/threads/{thread_id}/messages", b'{"question":"Hello"}')
        assert status == 200
        assert b'"type": "error"' in body
    finally:
        server.shutdown()
        worker.join()


def test_server_serves_local_markdown_dependencies(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        for path in ("/vendor/marked.esm.js", "/vendor/purify.min.js"):
            status, headers, body = request(server, "GET", path)
            assert status == 200
            assert headers["Content-Type"] == "text/javascript; charset=utf-8"
            assert body
    finally:
        server.shutdown()
        worker.join()


def test_server_serves_the_external_stylesheet(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, headers, body = request(server, "GET", "/app.css")
        assert status == 200
        assert headers["Content-Type"] == "text/css; charset=utf-8"
        assert b".app" in body
    finally:
        server.shutdown()
        worker.join()


def test_server_serves_the_persistent_chat_controls(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _, page = request(server, "GET", "/")
        assert status == 200
        assert b'id="research-depth"' in page
        status, _, script = request(server, "GET", "/app.js")
        assert status == 200
        assert b"/api/threads/${threadId}" in script
        assert b'method: "DELETE"' in script
        assert b"turn.answer_markdown" in script
        assert b"File Search/platform cost" in script
        assert b"Number.isFinite(diagnostics.latency_ms)" in script
        assert b"Show ${remaining} additional research result" in script
        assert b"Cited source" in script
        assert b"Retrieved passages from this source:" in script
        assert b"Additional research results" in script
        assert b"formatEvidenceTimestamp" in script
        assert b"markdown-table-scroll" in script
        assert b"Applied for future model replay" in script
        assert b"Assimilated orientation" in script
        assert b"knowledge_context" in script
        assert b"Orientation unavailable" in script
        assert b"record_ids" not in script
        assert b"anchor_ids" not in script
        assert b'event.type === "error"' in script
        assert b"Mentor unavailable. You can retry." in script
        status, _, stylesheet = request(server, "GET", "/app.css")
        assert b".markdown-table-scroll" in stylesheet
        assert b"overflow-wrap: normal" in stylesheet
        assert b".markdown-table-scroll { margin: 1rem 0; max-width: 100%; overflow-x: auto; width: 100%; }" in stylesheet
        assert b"#threads { min-width: 0;" in stylesheet
    finally:
        server.shutdown()
        worker.join()


def test_server_restores_only_safe_display_turns_and_permanently_deletes_one_thread(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")
    storage.register_source(
        relative_path="lesson.txt",
        filename="lesson.txt",
        year=2026,
        local_path=str(tmp_path / "lesson.txt"),
        modified_at=1.0,
        file_id="file_jacob",
        vector_store_file_id="vsf_jacob",
    )
    thread_id = storage.create_thread("Question")
    storage.append_thread_items(
        thread_id,
        [
            {"role": "user", "content": [{"type": "input_text", "text": "Question"}]},
            {"type": "reasoning", "encrypted_content": "must never reach the browser"},
        ],
    )
    storage.record_display_turn(
        thread_id,
        user_text="Question",
        answer_markdown="Answer",
        citations=[],
        evidence=[],
        diagnostics={"model": "gpt-5.6-sol"},
        response_id="resp_1",
        status="completed",
        incomplete_reason=None,
    )
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _, body = request(server, "GET", f"/api/threads/{thread_id}")
        payload = json.loads(body)
        assert status == 200
        assert payload == {
            "id": thread_id,
            "title": "Question",
            "turns": [
                {
                    "turn_number": 1,
                    "user_text": "Question",
                    "answer_markdown": "Answer",
                    "citations": [],
                    "evidence": [],
                    "diagnostics": {"model": "gpt-5.6-sol"},
                    "response_id": "resp_1",
                    "status": "completed",
                    "incomplete_reason": None,
                }
            ],
        }
        assert b"encrypted_content" not in body
        assert b"must never reach" not in body
        assert request(server, "GET", "/api/threads/999")[0] == 404

        status, _, body = request(server, "DELETE", f"/api/threads/{thread_id}")
        assert status == 200
        assert json.loads(body) == {"deleted": True}
        assert request(server, "GET", f"/api/threads/{thread_id}")[0] == 404
        assert storage.source_count() == 1
        assert storage.vector_store_id() == "vs_jacob"
        assert request(server, "DELETE", f"/api/threads/{thread_id}")[0] == 404
        assert request(server, "DELETE", "/api/threads/not-an-id")[0] == 404
    finally:
        server.shutdown()
        worker.join()

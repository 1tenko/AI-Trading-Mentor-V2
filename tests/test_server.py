import http.client
import json
import threading

from mentor.chat_service import Answer, StreamEvent
from mentor.profile import ProfileService
from mentor.server import create_server
from mentor.storage import Storage


class FakeChatService:
    def reply(self, thread_id, question):
        return Answer(text=f"Answer: {question}", citations=[], evidence=[])


class StreamingFakeChatService:
    def __init__(self):
        self.evaluation = None
        self.include_approved_notes = None

    def stream_reply(self, thread_id, question, evaluation, *, include_approved_notes=False):
        self.evaluation = evaluation
        self.include_approved_notes = include_approved_notes
        yield StreamEvent("delta", "A")
        yield StreamEvent("complete", answer=Answer(text="A", citations=[], evidence=[]))


class FailingStreamingFakeChatService:
    def stream_reply(self, thread_id, question, evaluation, *, include_approved_notes=False):
        yield StreamEvent("error", error="The mentor request failed. Try again.")


class ExplodingStreamingFakeChatService:
    def stream_reply(self, thread_id, question, evaluation, *, include_approved_notes=False):
        raise RuntimeError("simulated server failure")


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*server.server_address)
    request_headers = {"Content-Type": "application/json"} if body is not None else {}
    request_headers.update(headers or {})
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    result = (response.status, dict(response.getheaders()), response.read())
    connection.close()
    return result


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
        assert chat_service.include_approved_notes is False
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
        assert b'event.type === "error"' in script
        assert b"Mentor unavailable. You can retry." in script
        assert b"showProfileUpdate" in script
        assert b"Profile update needs confirmation" in script
        assert b"Review in Trader Profile" in script
        assert b"profile_update" in script
        assert b"clearPendingChatInteraction" in script
        assert b"if (activeThreadId !== threadId) return;" in script
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
            "dataset_scope": None,
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


def test_server_keeps_profile_acknowledgement_safe_and_historical_only(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Question")
    storage.record_display_turn(
        thread_id,
        user_text="Remember that I only trade ES.",
        answer_markdown="Done.",
        citations=[],
        evidence=[],
        diagnostics=None,
        response_id=None,
        status="completed",
        incomplete_reason=None,
        profile_update={"kind": "proposed"},
    )
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _, body = request(server, "GET", f"/api/threads/{thread_id}")
        assert status == 200
        profile_update = json.loads(body)["turns"][0]["profile_update"]
        assert profile_update == {"kind": "proposed"}
        assert "ES" not in json.dumps(profile_update)
        assert "tool_call" not in json.dumps(profile_update)
        assert storage.current_confirmed_profile_items() == []
    finally:
        server.shutdown()
        worker.join()


def test_chat_navigates_to_the_dedicated_trader_profile_page_without_technical_editor(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _, page = request(server, "GET", "/")
        assert status == 200
        assert b'href="/profile"' in page
        assert b">Trader Profile<" in page
        assert b'profile-panel' not in page
        assert b'profile-add-form' not in page
        assert b">Category<" not in page
        assert b">Kind<" not in page
        assert b">Provenance<" not in page

        status, _, script = request(server, "GET", "/app.js")
        assert status == 200
        assert b'window.location.assign("/profile")' in script
        assert b'profileRequest("/api/profile")' not in script
        assert b'profile-add-form' not in script

        status, _, stylesheet = request(server, "GET", "/app.css")
        assert status == 200
        assert b".profile-main" in stylesheet
        assert b".questionnaire-field" in stylesheet
        assert b"padding: 1.5rem 1rem" in stylesheet
        assert b"calc((100vw - 52rem)" not in stylesheet
        assert b"@media (max-width: 760px)" in stylesheet
    finally:
        server.shutdown()
        worker.join()


def test_profile_api_projects_safe_groups_and_applies_explicit_lifecycle_actions(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    profile = ProfileService(storage)
    current = profile.create_item(
        category="schedule/horizon",
        subject="Available session",
        value="London open",
        kind="constraint",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    proposal = profile.propose_item(
        category="goals/research",
        subject="Research goal",
        value="Study reversals",
        kind="goal",
        origin_kind="chat",
    )
    hidden_thread = storage.create_thread("Private profile origin")
    storage.append_thread_items(
        hidden_thread, [{"type": "reasoning", "encrypted_content": "never expose this"}]
    )
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _, body = request(server, "GET", "/api/profile")
        payload = json.loads(body)
        assert status == 200
        assert [item["id"] for item in payload["current"]] == [current.id]
        assert [item["id"] for item in payload["tentative"]] == [proposal.id]
        assert payload["history"] == []
        assert payload["conflicts"] == []
        assert b"encrypted_content" not in body
        assert b"never expose this" not in body

        status, _, body = request(
            server,
            "POST",
            "/api/profile/items",
            b'{"category":"markets/instruments","subject":"Primary market","value":"ES","kind":"preference","provenance":"USER_DECISION"}',
        )
        created = json.loads(body)["item"]
        assert status == 201
        assert (created["state"], created["provenance"], created["origin_kind"]) == (
            "confirmed",
            "USER_DECISION",
            "profile-editor",
        )

        status, _, body = request(
            server,
            "PATCH",
            f"/api/profile/items/{current.id}",
            b'{"action":"edit","value":"New York open","provenance":"USER_DECISION"}',
        )
        edited = json.loads(body)["item"]
        assert status == 200
        assert edited["supersedes_item_id"] == current.id
        assert storage.profile_item(current.id).state == "superseded"

        status, _, body = request(
            server, "PATCH", f"/api/profile/items/{proposal.id}", b'{"action":"confirm"}'
        )
        assert status == 200
        assert json.loads(body)["item"]["provenance"] == "USER_CONFIRMED"

        rejected = profile.propose_item(
            category="experience/learning",
            subject="Learning state",
            value="Needs more chart time",
            kind="learning-state",
            origin_kind="chat",
        )
        assert request(
            server, "PATCH", f"/api/profile/items/{rejected.id}", b'{"action":"reject"}'
        )[0] == 200
        assert storage.profile_item(rejected.id).state == "archived"

        conflict_first = profile.propose_item(
            category="style/methodology",
            subject="Entry style",
            value="I prefer breakouts.",
            kind="preference",
            origin_kind="chat",
        )
        conflict_second = profile.propose_item(
            category="style/methodology",
            subject="Entry style",
            value="I prefer mean reversion.",
            kind="preference",
            origin_kind="chat",
        )

        status, _, body = request(
            server,
            "PATCH",
            f"/api/profile/items/{conflict_first.id}",
            json.dumps({"action": "conflict", "item_ids": [conflict_first.id, conflict_second.id]}).encode(),
        )
        assert (status, json.loads(body)) == (200, {"updated": 2})
        status, _, body = request(server, "GET", "/api/profile")
        assert status == 200
        assert {item["id"] for item in json.loads(body)["conflicts"]} == {conflict_first.id, conflict_second.id}
        status, _, body = request(
            server, "PATCH", f"/api/profile/items/{conflict_first.id}", b'{"action":"resolve"}'
        )
        resolved = json.loads(body)["item"]
        assert (status, resolved["state"], resolved["provenance"], resolved["supersedes_item_id"]) == (
            200,
            "confirmed",
            "USER_DECISION",
            conflict_first.id,
        )
    finally:
        server.shutdown()
        worker.join()


def test_profile_api_rejects_malformed_oversized_unknown_and_stale_mutations(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    item = ProfileService(storage).create_item(
        category="execution/risk/constraints",
        subject="Maximum risk",
        value="One percent",
        kind="constraint",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="profile-editor",
    )
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        assert request(server, "POST", "/api/profile/items", b"not json")[0] == 400
        assert request(
            server,
            "POST",
            "/api/profile/items",
            b'{"category":"schedule/horizon","subject":"Risk","value":"One","kind":"constraint","state":"confirmed"}',
        )[0] == 400
        assert request(
            server,
            "POST",
            "/api/profile/items",
            b'{"category":"schedule/horizon","subject":"Risk","value":"One","kind":"constraint","provenance":"UNKNOWN"}',
        )[0] == 400
        assert request(
            server,
            "POST",
            "/api/profile/items",
            json.dumps(
                {
                    "category": "schedule/horizon",
                    "subject": "Available session",
                    "value": "x" * 501,
                    "kind": "constraint",
                }
            ).encode(),
        )[0] == 400
        assert request(server, "PATCH", f"/api/profile/items/{item.id}", b'{"action":"unknown"}')[0] == 400
        assert request(server, "PATCH", "/api/profile/items/999", b'{"action":"archive"}')[0] == 404
        assert request(server, "PATCH", f"/api/profile/items/{item.id}", b'{"action":"archive"}')[0] == 200
        status, _, body = request(server, "PATCH", f"/api/profile/items/{item.id}", b'{"action":"archive"}')
        assert status == 400
        assert b"confirmed" in body
    finally:
        server.shutdown()
        worker.join()


def test_profile_api_permanent_delete_does_not_cross_the_thread_boundary(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Origin")
    item = ProfileService(storage).create_item(
        category="schedule/horizon",
        subject="Available session",
        value="London open",
        kind="constraint",
        provenance="USER_STATED",
        state="confirmed",
        origin_kind="chat",
        origin_thread_id=thread_id,
        origin_turn_number=1,
        origin_available=True,
    )
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        assert request(server, "DELETE", f"/api/threads/{thread_id}")[0] == 200
        status, _, body = request(server, "GET", "/api/profile")
        assert status == 200
        assert json.loads(body)["current"][0]["origin_available"] is False
        assert request(server, "DELETE", f"/api/profile/items/{item.id}")[0] == 200
        assert request(server, "GET", "/api/profile")[2] == b'{"current": [], "tentative": [], "history": [], "conflicts": []}'
        assert request(server, "DELETE", f"/api/profile/items/{item.id}")[0] == 404
    finally:
        server.shutdown()
        worker.join()


def test_questionnaire_profile_page_and_local_batch_api_hide_internal_schema(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    server = create_server(storage, FakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, _, page = request(server, "GET", "/profile")
        assert status == 200
        assert b"Trader Profile" in page
        assert b"AI Chat" in page
        assert b"Category" not in page
        assert b"questionnaire-form" in page
        assert b"/profile.js" in page

        status, _, body = request(server, "GET", "/api/profile/questionnaire")
        payload = json.loads(body)
        assert status == 200
        assert len(payload["fields"]) == 21
        assert {"category", "kind", "provenance", "subject_key"}.isdisjoint(payload["fields"][0])

        status, _, body = request(
            server,
            "PUT",
            "/api/profile/questionnaire",
            json.dumps({"answers": {"q1": "Build consistency.", "q4": "idk", "q5": ""}}).encode(),
        )
        assert status == 200
        assert json.loads(body)["answers"]["q1"]["value"] == "Build consistency."
        assert json.loads(body)["answers"]["q4"]["unknown"] is True
        assert storage.current_confirmed_profile_items()[0].provenance == "USER_STATED"

        status, _, body = request(server, "GET", "/api/profile")
        assert status == 200
        assert json.loads(body)["current"][0]["questionnaire"] is True
    finally:
        server.shutdown()
        worker.join()


def test_data_workspace_loopback_api_imports_maps_and_scopes_one_thread(tmp_path):
    class DatasetAwareChat:
        def __init__(self):
            self.include_approved_notes = None

        def stream_reply(self, thread_id, question, evaluation, *, include_approved_notes=False):
            self.include_approved_notes = include_approved_notes
            yield StreamEvent("complete", answer=Answer(text="Local answer", citations=[], evidence=[]))

    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Analysis")
    chat = DatasetAwareChat()
    server = create_server(storage, chat, port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        csv = b"Result,Outcome,Journal\n1,Win,Waited for confirmation\n-1,Loss,Entered early\n"
        status, _, body = request(
            server,
            "POST",
            "/api/datasets/import",
            csv,
            headers={"Content-Type": "application/octet-stream", "X-Dataset-Filename": "trades.csv"},
        )
        imported = json.loads(body)
        assert status == 201
        assert imported["dataset"]["original_name"] == "trades.csv"
        dataset_id = imported["dataset"]["id"]
        assert b"Waited for confirmation" not in body

        status, _, body = request(server, "GET", f"/api/datasets/{dataset_id}")
        inspection = json.loads(body)
        assert status == 200
        assert inspection["preview"][0]["Journal"] == "Waited for confirmation"
        assert inspection["suggestions"][0] == {"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R"}

        mapping = {
            "entries": [
                {"column_ordinal": 0, "semantic_role": "trade_return", "unit": "R"},
                {"column_ordinal": 1, "semantic_role": "trade_outcome", "analysis_label": "Outcome", "model_disclosure": True},
                {"column_ordinal": 2, "analysis_label": "Journal", "mentor_access": "allow_row_values_when_analysing_notes"},
            ]
        }
        status, _, body = request(server, "POST", f"/api/datasets/{dataset_id}/mapping", json.dumps(mapping).encode())
        confirmed = json.loads(body)
        assert status == 201
        assert confirmed["mapping"]["status"] == "confirmed"
        assert confirmed["entries"][2]["mentor_access"] == "allow_row_values_when_analysing_notes"
        assert confirmed["entries"][1]["aggregate_labels_allowed"] is True

        status, _, body = request(
            server, "PUT", f"/api/threads/{thread_id}/dataset", json.dumps({"dataset_id": dataset_id}).encode()
        )
        assert status == 200
        assert json.loads(body)["dataset_scope"]["original_name"] == "trades.csv"
        status, _, body = request(server, "GET", f"/api/threads/{thread_id}")
        assert status == 200
        assert json.loads(body)["dataset_scope"]["mapping_status"] == "confirmed"
        status, _, body = request(server, "POST", "/api/threads", b'{"title":"Separate"}')
        assert status == 201
        assert json.loads(body)["id"] != thread_id
        status, _, body = request(server, "GET", "/api/threads/2")
        assert status == 200
        assert json.loads(body)["dataset_scope"] is None

        status, _, _ = request(
            server,
            "POST",
            f"/api/threads/{thread_id}/messages",
            json.dumps({"question": "Use the approved notes.", "include_approved_notes": True}).encode(),
        )
        assert status == 200
        assert chat.include_approved_notes is True

        status, _, body = request(server, "PUT", f"/api/threads/{thread_id}/dataset", b'{"dataset_id":null}')
        assert status == 200
        assert json.loads(body)["dataset_scope"] is None
        assert request(server, "GET", "/api/datasets")[0] == 200
    finally:
        server.shutdown()
        worker.join()


def test_chat_attachment_auto_confirms_only_safe_mapping_and_scopes_its_thread(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Analysis")
    server = create_server(storage, StreamingFakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        csv = (
            b"Result_R,MFE_R,MAE_R,Entry_Timestamp,Instrument,Session,Direction,Outcome,Setup,Quarter,Notes\n"
            b"1,2,-1,2026-01-02T09:30:00,ES,NYAM,Long,Win,Reversal,Q1,Waited for confirmation\n"
        )
        status, _, body = request(
            server,
            "POST",
            f"/api/threads/{thread_id}/attachments",
            csv,
            headers={"Content-Type": "application/octet-stream", "X-Dataset-Filename": "gxt_backtest.csv"},
        )
        attached = json.loads(body)
        assert status == 201
        assert attached["state"] == "ready"
        assert attached["dataset_scope"]["original_name"] == "gxt_backtest.csv"
        assert attached["mapping"]["status"] == "confirmed"
        assert {entry.source for entry in storage.mapping_entries(attached["mapping"]["id"])} == {"deterministic_auto"}
        assert next(entry for entry in storage.mapping_entries(attached["mapping"]["id"]) if entry.analysis_label == "Trade notes").mentor_access == "aggregates_only"
        assert all("Notes" not in value for value in body.decode().splitlines())

        status, _, body = request(
            server,
            "POST",
            f"/api/threads/{thread_id}/attachments",
            csv,
            headers={"Content-Type": "application/octet-stream", "X-Dataset-Filename": "other.csv"},
        )
        assert status == 409
        assert json.loads(body)["state"] == "replace_required"
    finally:
        server.shutdown()
        worker.join()


def test_chat_attachment_keeps_ambiguous_pnl_local_and_unscoped(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    thread_id = storage.create_thread("Analysis")
    server = create_server(storage, StreamingFakeChatService(), port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        safe_csv = b"Result_R,Session\n1,NYAM\n"
        assert request(
            server,
            "POST",
            f"/api/threads/{thread_id}/attachments",
            safe_csv,
            headers={"Content-Type": "application/octet-stream", "X-Dataset-Filename": "current.csv"},
        )[0] == 201
        status, _, body = request(
            server,
            "POST",
            f"/api/threads/{thread_id}/attachments",
            b"PnL,Session\n10,NYAM\n",
            headers={"Content-Type": "application/octet-stream", "X-Dataset-Filename": "ambiguous.csv", "X-Replace-Attachment": "true"},
        )
        attached = json.loads(body)
        assert status == 422
        assert attached["state"] == "needs_input"
        assert attached["dataset_scope"]["original_name"] == "current.csv"
        assert attached["clarifications"] == [{"column_ordinal": 0, "header": "PnL", "role": "trade_return"}]
        assert storage.thread_dataset_scope(thread_id).dataset_id == attached["dataset_scope"]["dataset_id"]
    finally:
        server.shutdown()
        worker.join()

import http.client
import threading

from mentor.chat_service import Answer, StreamEvent
from mentor.server import create_server
from mentor.storage import Storage


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


def request(server, method, path, body=None):
    connection = http.client.HTTPConnection(*server.server_address)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection.request(method, path, body=body, headers=headers)
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
            b'{"question":"Hello","evaluation":{"reasoning_effort":"xhigh","reasoning_mode":"pro"}}',
        )
        assert status == 200
        assert headers["Content-Type"] == "text/event-stream; charset=utf-8"
        assert b'"type": "delta"' in body
        assert b'"type": "complete"' in body
        assert chat_service.evaluation.reasoning_effort == "xhigh"
        assert chat_service.evaluation.reasoning_mode == "pro"
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

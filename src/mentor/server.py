"""Private loopback HTTP server for the Phase 1 browser chat."""

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mentor.chat_service import Answer
from mentor.storage import Storage

MAX_JSON_BYTES = 16_384
FILE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def create_server(storage: Storage, chat_service: Any, port: int = 8765) -> ThreadingHTTPServer:
    """Create a server bound exclusively to this computer's loopback address."""

    class Handler(_Handler):
        pass

    Handler.storage = storage
    Handler.chat_service = chat_service
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


class _Handler(BaseHTTPRequestHandler):
    storage: Storage
    chat_service: Any

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self._send_bytes(HTTPStatus.OK, _static("index.html"), "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._send_bytes(HTTPStatus.OK, _static("app.js"), "text/javascript; charset=utf-8")
            return
        if path == "/api/threads":
            self._send_json(
                HTTPStatus.OK,
                {"threads": [thread.__dict__ for thread in self.storage.threads()]},
            )
            return
        match = re.fullmatch(r"/api/sources/([^/]+)", path)
        if match and FILE_ID.fullmatch(unquote(match.group(1))):
            self._source(unquote(match.group(1)))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._json_body()
            if path == "/api/threads":
                title = _title(body.get("title"))
                thread_id = self.storage.create_thread(title)
                self._send_json(HTTPStatus.CREATED, {"id": thread_id, "title": title})
                return
            match = re.fullmatch(r"/api/threads/(\d+)/messages", path)
            if match:
                question = body.get("question")
                if not isinstance(question, str):
                    raise ValueError("Question must be text.")
                thread_id = int(match.group(1))
                if not self.storage.has_thread(thread_id):
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Conversation not found."})
                    return
                if hasattr(self.chat_service, "stream_reply"):
                    self._stream_answer(thread_id, question)
                else:
                    self._send_json(HTTPStatus.OK, _answer_json(self.chat_service.reply(thread_id, question)))
                return
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except Exception:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "The mentor is unavailable."})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def _source(self, file_id: str) -> None:
        source = self.storage.source_for_file(file_id)
        if source is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Source not found."})
            return
        path = Path(source.local_path)
        if not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Source is unavailable locally."})
            return
        self._send_bytes(HTTPStatus.OK, path.read_bytes(), "text/plain; charset=utf-8")

    def _stream_answer(self, thread_id: int, question: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        for event in self.chat_service.stream_reply(thread_id, question):
            body = {"type": event.type, "text": event.text}
            if event.answer is not None:
                body["answer"] = _answer_json(event.answer)
            self.wfile.write(f"data: {json.dumps(body)}\n\n".encode())
            self.wfile.flush()

    def _json_body(self) -> dict:
        content_length = self.headers.get("Content-Length")
        if content_length is None or not content_length.isdigit():
            raise ValueError("A JSON request body is required.")
        length = int(content_length)
        if length > MAX_JSON_BYTES:
            raise ValueError("Request is too large.")
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Request must be valid JSON.") from None
        if not isinstance(body, dict):
            raise ValueError("Request must be a JSON object.")
        return body

    def _send_json(self, status: HTTPStatus, body: dict) -> None:
        self._send_bytes(status, json.dumps(body).encode(), "application/json; charset=utf-8")

    def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep private prompts and source paths out of the terminal."""


def _static(filename: str) -> bytes:
    return (Path(__file__).parent / "static" / filename).read_bytes()


def _title(value: object) -> str:
    if not isinstance(value, str) or not (title := value.strip()):
        raise ValueError("Thread title cannot be blank.")
    if len(title) > 120:
        raise ValueError("Thread title is too long.")
    return title


def _answer_json(answer: Answer) -> dict:
    return {
        "text": answer.text,
        "citations": [citation.__dict__ for citation in answer.citations],
        "evidence": [evidence.__dict__ for evidence in answer.evidence],
    }

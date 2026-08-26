"""Private loopback HTTP server for the Phase 1 browser chat."""

import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mentor.chat_service import Answer, EvaluationConfig, StreamEvent
from mentor.profile import QUESTIONNAIRE_FIELDS, ProfileService, ProfileValidationError
from mentor.storage import Storage

LOGGER = logging.getLogger(__name__)
MAX_JSON_BYTES = 16_384
FILE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
STATIC_ASSETS = {
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/profile.js": ("profile.js", "text/javascript; charset=utf-8"),
    "/vendor/marked.esm.js": ("vendor/marked.esm.js", "text/javascript; charset=utf-8"),
    "/vendor/purify.min.js": ("vendor/purify.min.js", "text/javascript; charset=utf-8"),
}
QUESTIONNAIRE_SUBJECTS = frozenset(
    (field.category, " ".join(field.subject.split()).casefold())
    for field in QUESTIONNAIRE_FIELDS
)


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
        if path in STATIC_ASSETS:
            filename, content_type = STATIC_ASSETS[path]
            self._send_bytes(HTTPStatus.OK, _static(filename), content_type)
            return
        if path == "/api/threads":
            self._send_json(
                HTTPStatus.OK,
                {"threads": [thread.__dict__ for thread in self.storage.threads()]},
            )
            return
        if path == "/profile":
            self._send_bytes(HTTPStatus.OK, _static("profile.html"), "text/html; charset=utf-8")
            return
        if path == "/api/profile":
            groups = {"current": [], "tentative": [], "history": [], "conflicts": []}
            for item in self.storage.profile_items():
                groups[_profile_group(item.state)].append(_profile_item_json(item))
            self._send_json(HTTPStatus.OK, groups)
            return
        if path == "/api/profile/questionnaire":
            self._send_json(HTTPStatus.OK, _questionnaire_json(ProfileService(self.storage)))
            return
        match = re.fullmatch(r"/api/threads/(\d+)", path)
        if match:
            thread = self.storage.thread(int(match.group(1)))
            if thread is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Conversation not found."})
                return
            self._send_json(
                HTTPStatus.OK,
                {"id": thread.id, "title": thread.title, "turns": self.storage.display_turns(thread.id)},
            )
            return
        match = re.fullmatch(r"/api/sources/([^/]+)", path)
        if match and FILE_ID.fullmatch(unquote(match.group(1))):
            self._source(unquote(match.group(1)))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        profile_match = re.fullmatch(r"/api/profile/items/(\d+)", path)
        if profile_match:
            item_id = int(profile_match.group(1))
            try:
                ProfileService(self.storage).delete_item(item_id)
            except ProfileValidationError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Profile item not found."})
                return
            self._send_json(HTTPStatus.OK, {"deleted": True})
            return
        match = re.fullmatch(r"/api/threads/(\d+)", path)
        if match is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        if not self.storage.delete_thread(int(match.group(1))):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Conversation not found."})
            return
        self._send_json(HTTPStatus.OK, {"deleted": True})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._json_body()
            if path == "/api/threads":
                title = _title(body.get("title"))
                thread_id = self.storage.create_thread(title)
                self._send_json(HTTPStatus.CREATED, {"id": thread_id, "title": title})
                return
            if path == "/api/profile/items":
                item = _create_profile_item(ProfileService(self.storage), body)
                self._send_json(HTTPStatus.CREATED, {"item": _profile_item_json(item)})
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
                    self._stream_answer(thread_id, question, _evaluation(body.get("evaluation")))
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

    def do_PATCH(self) -> None:  # noqa: N802
        match = re.fullmatch(r"/api/profile/items/(\d+)", urlparse(self.path).path)
        if match is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        item_id = int(match.group(1))
        profile = ProfileService(self.storage)
        try:
            if self.storage.profile_item(item_id) is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Profile item not found."})
                return
            result = _update_profile_item(profile, item_id, self._json_body())
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except Exception:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "The mentor is unavailable."})
            return
        if isinstance(result, int):
            self._send_json(HTTPStatus.OK, {"updated": result})
        else:
            self._send_json(HTTPStatus.OK, {"item": _profile_item_json(result)})

    def do_PUT(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/profile/questionnaire":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        try:
            body = self._json_body()
            _only_fields(body, {"answers"})
            answers = body.get("answers")
            if not isinstance(answers, dict):
                raise ValueError("Questionnaire answers must be an object.")
            ProfileService(self.storage).save_questionnaire_answers(answers)
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._send_json(HTTPStatus.OK, _questionnaire_json(ProfileService(self.storage)))

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

    def _stream_answer(
        self, thread_id: int, question: str, evaluation: EvaluationConfig
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            for event in self.chat_service.stream_reply(thread_id, question, evaluation):
                self._write_stream_event(event)
        except Exception as error:
            LOGGER.warning("Mentor SSE handler raised %s", type(error).__name__)
            self._write_stream_event(StreamEvent("error", error="The mentor request failed. Try again."))

    def _write_stream_event(self, event: Any) -> None:
        body = {"type": event.type, "text": event.text}
        if event.answer is not None:
            body["answer"] = _answer_json(event.answer)
        if event.incomplete_reason is not None:
            body["incomplete_reason"] = event.incomplete_reason
        if event.error:
            body["error"] = event.error
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
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'")
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
        "diagnostics": None if answer.diagnostics is None else answer.diagnostics.__dict__,
        "incomplete_reason": answer.incomplete_reason,
        "profile_update": answer.profile_update,
    }


def _evaluation(value: object) -> EvaluationConfig:
    if value is None:
        return EvaluationConfig()
    if not isinstance(value, dict):
        raise ValueError("Evaluation settings must be an object.")
    effort = value.get("reasoning_effort", "high")
    mode = value.get("reasoning_mode", "standard")
    research_depth = value.get("research_depth", "auto")
    if not isinstance(effort, str) or not isinstance(mode, str) or not isinstance(research_depth, str):
        raise ValueError("Evaluation settings must be text.")
    return EvaluationConfig(effort, mode, research_depth)


def _create_profile_item(profile: ProfileService, body: dict):
    _only_fields(body, {"category", "subject", "value", "kind", "provenance"})
    required = ("category", "subject", "value", "kind")
    if any(field not in body for field in required):
        raise ValueError("Profile category, subject, value, and kind are required.")
    provenance = body.get("provenance", "USER_STATED")
    if provenance not in {"USER_STATED", "USER_DECISION"}:
        raise ValueError("Direct profile items must use USER_STATED or USER_DECISION provenance.")
    return profile.create_item(
        category=body["category"],
        subject=body["subject"],
        value=body["value"],
        kind=body["kind"],
        provenance=provenance,
        state="confirmed",
        origin_kind="profile-editor",
    )


def _update_profile_item(profile: ProfileService, item_id: int, body: dict):
    action = body.get("action")
    if not isinstance(action, str):
        raise ValueError("Profile action must be text.")
    if action == "edit":
        _only_fields(body, {"action", "value", "provenance"})
        if "value" not in body:
            raise ValueError("Profile edit value is required.")
        provenance = body.get("provenance", "USER_DECISION")
        if provenance not in {"USER_STATED", "USER_DECISION"}:
            raise ValueError("Profile edits must use USER_STATED or USER_DECISION provenance.")
        return profile.supersede_item(
            item_id,
            value=body["value"],
            provenance=provenance,
            origin_kind="profile-editor",
        )
    if action == "confirm":
        _only_fields(body, {"action"})
        return profile.confirm_item(item_id, origin_kind="confirmation")
    if action == "reject":
        _only_fields(body, {"action"})
        if profile.storage.profile_item(item_id).state != "tentative":
            raise ProfileValidationError("only a tentative profile item can be rejected")
        profile.archive_item(item_id)
        return profile.storage.profile_item(item_id)
    if action == "archive":
        _only_fields(body, {"action"})
        if profile.storage.profile_item(item_id).state != "confirmed":
            raise ProfileValidationError("only a confirmed profile item can be archived")
        profile.archive_item(item_id)
        return profile.storage.profile_item(item_id)
    if action == "conflict":
        _only_fields(body, {"action", "item_ids"})
        item_ids = body.get("item_ids")
        if not isinstance(item_ids, list) or len(item_ids) < 2 or any(
            not isinstance(candidate, int) or candidate <= 0 for candidate in item_ids
        ):
            raise ValueError("Conflict item_ids must contain at least two positive item ids.")
        if item_id not in item_ids:
            raise ValueError("Conflict item_ids must include the route profile item.")
        return profile.conflict_items(item_ids)
    if action == "resolve":
        _only_fields(body, {"action"})
        return profile.resolve_conflict(item_id)
    raise ValueError("Unknown profile action.")


def _only_fields(body: dict, allowed: set[str]) -> None:
    unknown = set(body).difference(allowed)
    if unknown:
        raise ValueError("Unknown profile field.")


def _profile_group(state: str) -> str:
    if state == "confirmed":
        return "current"
    if state == "tentative":
        return "tentative"
    if state == "conflicting":
        return "conflicts"
    return "history"


def _questionnaire_json(profile: ProfileService) -> dict[str, object]:
    answers = profile.questionnaire_answers()
    return {
        "fields": [
            {
                "key": field.key,
                "section": field.section,
                "question": field.question,
                "helper": field.helper,
            }
            for field in QUESTIONNAIRE_FIELDS
        ],
        "answers": {
            key: None if answer is None else {"value": answer.item.value, "unknown": answer.unknown}
            for key, answer in answers.items()
        },
    }


def _profile_item_json(item: Any) -> dict[str, object]:
    return {
        "id": item.id,
        "category": item.category,
        "subject": item.subject,
        "value": item.value,
        "kind": item.kind,
        "provenance": item.provenance,
        "state": item.state,
        "origin_kind": item.origin_kind,
        "origin_thread_id": item.origin_thread_id,
        "origin_turn_number": item.origin_turn_number,
        "origin_available": item.origin_available,
        "supersedes_item_id": item.supersedes_item_id,
        "questionnaire": (item.category, item.subject_key) in QUESTIONNAIRE_SUBJECTS,
    }

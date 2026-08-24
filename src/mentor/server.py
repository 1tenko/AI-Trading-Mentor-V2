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
from mentor.derived_records import (
    Claim,
    ConflictUnresolved,
    Evolution,
    ProcedureSequenceHierarchy,
    Relationship,
    conclusion_lineage,
    conflict_side_lineages,
)
from mentor.knowledge import derived_provenance_label
from mentor.orientation import concept_summaries
from mentor.storage import Storage

LOGGER = logging.getLogger(__name__)
MAX_JSON_BYTES = 16_384
FILE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
STATIC_ASSETS = {
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/vendor/marked.esm.js": ("vendor/marked.esm.js", "text/javascript; charset=utf-8"),
    "/vendor/purify.min.js": ("vendor/purify.min.js", "text/javascript; charset=utf-8"),
}


def create_server(storage: Storage, chat_service: Any, port: int = 8765) -> ThreadingHTTPServer:
    """Create a server bound exclusively to this computer's loopback address."""
    service_storage = getattr(chat_service, "storage", None)
    if isinstance(service_storage, Storage) and (
        service_storage.database_path.resolve() != storage.database_path.resolve()
        or service_storage.runtime_scope != storage.runtime_scope
    ):
        raise ValueError("server and chat service must use the same runtime")

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
        if path == "/favicon.ico":
            self._send_bytes(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
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
        if path == "/api/knowledge":
            self._send_json(HTTPStatus.OK, _knowledge_overview(self.storage))
            return
        match = re.fullmatch(r"/api/knowledge/snapshots/([A-Za-z0-9_-]+)", path)
        if match:
            detail = _snapshot_detail(self.storage, match.group(1))
            if detail is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Knowledge snapshot not found."})
                return
            self._send_json(HTTPStatus.OK, detail)
            return
        match = re.fullmatch(r"/api/knowledge/snapshots/([A-Za-z0-9_-]+)/records/([A-Za-z0-9_-]+)", path)
        if match:
            detail = _record_detail(self.storage, match.group(1), match.group(2))
            if detail is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Knowledge record not found."})
                return
            self._send_json(HTTPStatus.OK, detail)
            return
        match = re.fullmatch(r"/api/knowledge/threads/(\d+)/orientation", path)
        if match:
            audits = self.storage.orientation_audits(int(match.group(1)))
            if audits is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Conversation not found."})
                return
            self._send_json(HTTPStatus.OK, {"thread_id": int(match.group(1)), "turns": audits})
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
        match = re.fullmatch(r"/api/threads/(\d+)", urlparse(self.path).path)
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


def _knowledge_overview(storage: Storage) -> dict:
    current = storage.current_snapshot()
    return {
        "collections": [
            {
                "collection_id": collection.collection_id,
                "display_name": collection.display_name,
                "domain": collection.domain,
                "enabled": collection.enabled,
                "scope": collection.scope,
            }
            for collection in storage.collections()
        ],
        "current_snapshot": None if current is None else _snapshot_summary(current),
        "snapshots": [_snapshot_summary(snapshot) for snapshot in storage.snapshots()],
        "pending_source_changes": [
            {
                "source_id": change.source_id,
                "lifecycle_state": change.lifecycle_state,
                "revision_id": change.revision_id,
                "observed_at": change.observed_at,
            }
            for change in storage.source_changes()
        ],
    }


def _snapshot_detail(storage: Storage, snapshot_id: str) -> dict | None:
    snapshot = storage.snapshot(snapshot_id)
    if snapshot is None:
        return None
    records = storage.derived_records(snapshot_id, include_stale=True)
    stale_ids = set(storage.stale_record_ids(snapshot_id))
    coverage = storage.snapshot_source_coverage(snapshot_id)
    gate = storage.candidate_gate(snapshot_id)
    occurrences = storage.orientation_concept_occurrences(snapshot_id)
    concepts = storage.orientation_concepts(snapshot_id)
    return {
        "snapshot": _snapshot_summary(snapshot),
        "compiler": {
            "model_version": snapshot.model_version,
            "prompt_version": snapshot.prompt_version,
            "schema_version": snapshot.schema_version,
        },
        "coverage": {
            "processed": sum(result.status == "processed" for result in coverage),
            "failed": sum(result.status == "failed" for result in coverage),
        },
        "candidate_gate": None if gate is None else {
            "status": gate.status,
            "checked_at": gate.checked_at,
            "has_failure_reason": gate.failure_reason is not None,
        },
        "metrics": [_metric_json(metric) for metric in storage.compilation_metrics(snapshot.run_id)],
        "records": [_record_summary(record, record.record_id in stale_ids) for record in records],
        "concepts": [_concept_summary_json(concept) for concept in concept_summaries(
            concepts,
            occurrences,
            (concept.concept_id for concept in concepts),
        )],
        "stale_record_ids": sorted(stale_ids),
    }


def _record_detail(storage: Storage, snapshot_id: str, record_id: str) -> dict | None:
    if storage.snapshot(snapshot_id) is None:
        return None
    records = storage.derived_records(snapshot_id, include_stale=True)
    record = next((value for value in records if value.record_id == record_id), None)
    if record is None:
        return None
    reused_from = storage.derived_record_reuse(snapshot_id).get(record_id)
    concept_ids = storage.orientation_concept_links(snapshot_id).get(record_id, ())
    lineage = conclusion_lineage(record, {value.record_id: value for value in records})
    return {
        **_record_summary(record, record_id in storage.stale_record_ids(snapshot_id)),
        "qualification": record.qualification,
        "facets": [{"name": facet.name, "value": facet.value} for facet in record.facets],
        "compiler_provenance": None if record.compiler_provenance is None else {
            "model_version": record.compiler_provenance.model_version,
            "prompt_version": record.compiler_provenance.prompt_version,
            "schema_version": record.compiler_provenance.schema_version,
        },
        "content": _typed_record_content(record),
        "anchors": [
            _inspector_anchor(anchor)
            for anchor in storage.source_anchor_metadata(record.anchors)
        ],
        "dependencies": [
            {"kind": dependency.kind}
            for dependency in record.dependencies
        ],
        "lineage": {
            "conclusion_record_id": lineage.conclusion_record_id,
            "input_record_ids": list(lineage.input_record_ids),
            "anchor_ids": list(lineage.anchor_ids),
            "source_revision_ids": list(lineage.source_revision_ids),
            "transitive_records": [
                {
                    "record_id": item.record_id,
                    "input_record_ids": list(item.input_record_ids),
                    "anchor_ids": list(item.anchor_ids),
                    "source_revision_ids": list(item.source_revision_ids),
                }
                for item in lineage.transitive_records
            ],
            "complete": lineage.complete,
            "conflict_sides": (
                [
                    {
                        "alternative": side.alternative,
                        "input_record_id": side.input_record_id,
                        "anchor_ids": list(side_lineage.anchor_ids),
                        "source_revision_ids": list(side_lineage.source_revision_ids),
                        "transitive_record_ids": [
                            item.record_id for item in side_lineage.transitive_records
                        ],
                    }
                    for side, side_lineage in conflict_side_lineages(
                        record, {value.record_id: value for value in records}
                    )
                ]
                if isinstance(record, ConflictUnresolved)
                else []
            ),
        },
        "concepts": [_concept_summary_json(concept) for concept in concept_summaries(
            storage.orientation_concepts(snapshot_id),
            storage.orientation_concept_occurrences(snapshot_id),
            concept_ids,
            record_id=record_id,
        )],
        "reused": reused_from is not None,
    }


def _concept_summary_json(concept: Any) -> dict:
    return {
        "canonical_label": concept.canonical_label,
        "aliases": list(concept.aliases),
        "scope": concept.scope,
        "supporting_record_count": concept.supporting_record_count,
        "supporting_anchor_count": concept.supporting_anchor_count,
        "occurrences": [
            {
                "role": occurrence.role,
                "position": occurrence.position,
                "label": occurrence.label,
            }
            for occurrence in concept.occurrences
        ],
    }


def _inspector_anchor(anchor: dict) -> dict:
    """Expose only human-verifiable locator fields, never opaque identity/hash data."""
    return {
        key: anchor.get(key)
        for key in (
            "filename",
            "lesson_title",
            "author",
            "course",
            "year",
            "timestamp_start_ms",
            "timestamp_end_ms",
        )
    }


def _snapshot_summary(snapshot: Any) -> dict:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "run_id": snapshot.run_id,
        "selected_revision_fingerprint": snapshot.selected_revision_fingerprint,
        "source_count": len(snapshot.selected_revision_ids),
        "status": snapshot.status,
        "created_at": snapshot.created_at,
        "validated_at": snapshot.validated_at,
        "published_at": snapshot.published_at,
        "failed_at": snapshot.failed_at,
        "has_failure_reason": snapshot.failure_reason is not None,
        "raw_store_ready": snapshot.raw_store_id is not None,
        "derived_store_ready": snapshot.derived_store_id is not None,
    }


def _record_summary(record: Any, stale: bool) -> dict:
    return {
        "record_id": record.record_id,
        "family": record.family,
        "derived_kind": record.derived_kind,
        "semantic_subtype": record.semantic_subtype,
        "evidence_state": record.evidence_state,
        "validation_state": record.validation_state,
        "lifecycle_state": record.lifecycle_state,
        "provenance_label": derived_provenance_label(
            evidence_state=record.evidence_state, validation_state=record.validation_state
        ),
        "stale": stale,
    }


def _typed_record_content(record: Any) -> dict:
    if isinstance(record, Claim):
        return {"subject": record.subject, "predicate": record.predicate, "object": record.object}
    if isinstance(record, Relationship):
        return {"left": record.left, "relation": record.relation, "right": record.right}
    if isinstance(record, ProcedureSequenceHierarchy):
        return {
            "kind": record.kind,
            "terms": list(record.terms),
            "prerequisites": list(record.prerequisites),
            "conditions": list(record.conditions),
            "branches": [
                {"condition": branch.condition, "steps": list(branch.steps)}
                for branch in record.branches
            ],
        }
    if isinstance(record, Evolution):
        return {
            "subject": record.subject,
            "previous": record.previous,
            "current": record.current,
            "classification": record.classification,
            "negative_evidence_state": record.negative_evidence_state,
            "earlier_observed_years": list(record.earlier_observed_years),
            "later_observed_years": list(record.later_observed_years),
            "competing_anchor_count": len(record.competing_anchors),
            "deprecation_evidence_anchor_count": len(record.deprecation_evidence_anchors),
        }
    if isinstance(record, ConflictUnresolved):
        return {
            "kind": record.kind,
            "subject": record.subject,
            "alternatives": list(record.alternatives),
            "competing_record_count": len(record.competing_record_ids),
            "reconciliation_state": record.reconciliation_state,
            "relevant_scopes": list(record.relevant_scopes),
            "conditions": list(record.conditions),
            "unresolved_questions": list(record.unresolved_questions),
        }
    raise ValueError("unknown derived record family")


def _metric_json(metric: Any) -> dict:
    return {
        "stage": metric.stage,
        "source_count": metric.source_count,
        "record_count": metric.record_count,
        "call_count": metric.call_count,
        "input_tokens": metric.input_tokens,
        "output_tokens": metric.output_tokens,
        "reasoning_tokens": metric.reasoning_tokens,
        "latency_ms": metric.latency_ms,
        "cost_usd": metric.cost_usd,
        "remote_calls": metric.remote_calls,
        "failure_count": metric.failure_count,
        "model_version": metric.model_version,
        "prompt_version": metric.prompt_version,
        "schema_version": metric.schema_version,
    }

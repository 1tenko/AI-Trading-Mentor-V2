from types import SimpleNamespace

import pytest

from mentor.config import ConfigError, load_config
from mentor.vector_stores import FileExpiration, VectorStoreAdapter, VectorStoreExpiration


class FakeVectorStoreClient:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.attachment_error: Exception | None = None
        self.attachment_response = SimpleNamespace(id="vsf_candidate", status="in_progress")
        self.retrieved_attachment_response = SimpleNamespace(id="vsf_candidate", status="completed")
        self.batch_response = SimpleNamespace(
            id="vsfb_candidate", status="in_progress", file_counts={"completed": 1}
        )
        self.retrieved_batch_response = SimpleNamespace(
            id="vsfb_candidate", status="completed", file_counts={"completed": 2}
        )
        self.vector_store_files = SimpleNamespace(
            create=self.attach,
            retrieve=self.retrieve,
            delete=self.detach,
        )
        self.file_batches = SimpleNamespace(create=self.create_batch, retrieve=self.retrieve_batch)
        self.vector_stores = SimpleNamespace(
            create=self.create_store,
            retrieve=self.retrieve_store,
            files=self.vector_store_files,
            file_batches=self.file_batches,
            search=self.search,
        )
        self.files = SimpleNamespace(create=self.upload)

    def create_store(self, **kwargs):
        self.calls.append(("create_store", (), kwargs))
        return SimpleNamespace(id="vs_candidate", status="completed", created_at=1, expires_at=86_401, usage_bytes=1234)

    def retrieve_store(self, vector_store_id):
        self.calls.append(("retrieve_store", (vector_store_id,), {}))
        return SimpleNamespace(id=vector_store_id, status="completed", created_at=1, expires_at=86_401, usage_bytes=4321)

    def upload(self, **kwargs):
        self.calls.append(("upload", (), kwargs))
        return SimpleNamespace(id="file_derived", bytes=42, created_at=1, expires_at=86_401)

    def attach(self, vector_store_id, **kwargs):
        self.calls.append(("attach", (vector_store_id,), kwargs))
        if self.attachment_error:
            raise self.attachment_error
        return self.attachment_response

    def retrieve(self, file_id, **kwargs):
        self.calls.append(("retrieve", (file_id,), kwargs))
        return self.retrieved_attachment_response

    def create_batch(self, vector_store_id, **kwargs):
        self.calls.append(("create_batch", (vector_store_id,), kwargs))
        return self.batch_response

    def retrieve_batch(self, batch_id, **kwargs):
        self.calls.append(("retrieve_batch", (batch_id,), kwargs))
        return self.retrieved_batch_response

    def detach(self, file_id, **kwargs):
        self.calls.append(("detach", (file_id,), kwargs))
        return SimpleNamespace(id=file_id, deleted=True)

    def search(self, vector_store_id, **kwargs):
        self.calls.append(("search", (vector_store_id,), kwargs))
        return SimpleNamespace(data=[
            SimpleNamespace(
                file_id="file_orientation",
                filename="orientation.json",
                score=0.92,
                attributes={"snapshot_id": "snap_current", "record_id": "rec_1"},
                content=[SimpleNamespace(type="text", text="A concise orientation artifact.")],
            )
        ])


def test_adapter_maps_store_attachment_and_batch_statuses_without_a_real_client():
    client = FakeVectorStoreClient()
    adapter = VectorStoreAdapter(client)

    store = adapter.create_store("Candidate", {"snapshot_id": "snap_candidate"})
    attachment = adapter.attach_file(store.store_id, "file_orientation", {"record_id": "rec_1"})
    attached = adapter.attachment_status(store.store_id, "file_orientation")
    batch = adapter.create_batch(store.store_id, ["file_a", "file_b"], {"snapshot_id": "snap_candidate"})
    completed = adapter.batch_status(store.store_id, batch.batch_id)

    assert store.store_id == "vs_candidate"
    assert attachment.status == "in_progress"
    assert attached.status == "completed"
    assert batch.file_counts == {"completed": 1}
    assert completed.status == "completed"
    assert client.calls == [
        ("create_store", (), {"name": "Candidate", "metadata": {"snapshot_id": "snap_candidate"}}),
        ("attach", ("vs_candidate",), {"file_id": "file_orientation", "attributes": {"record_id": "rec_1"}}),
        ("retrieve", ("file_orientation",), {"vector_store_id": "vs_candidate"}),
        ("create_batch", ("vs_candidate",), {"file_ids": ["file_a", "file_b"], "attributes": {"snapshot_id": "snap_candidate"}}),
        ("retrieve_batch", ("vsfb_candidate",), {"vector_store_id": "vs_candidate"}),
    ]


def test_adapter_passes_caller_owned_expiry_and_maps_usage_without_touching_existing_files():
    client = FakeVectorStoreClient()
    adapter = VectorStoreAdapter(client)

    store = adapter.create_store(
        "Pilot", {"artifact_scope": "pilot"},
        expires_after=VectorStoreExpiration("last_active_at", 1),
    )
    uploaded = adapter.upload_text(
        "derived.json", "{}", expires_after=FileExpiration("created_at", 86_400)
    )
    refreshed = adapter.retrieve_store(store.store_id)

    assert store.expires_at == 86_401
    assert uploaded.expires_at == 86_401
    assert uploaded.bytes == 42
    assert refreshed.usage_bytes == 4321
    assert client.calls == [
        ("create_store", (), {"name": "Pilot", "metadata": {"artifact_scope": "pilot"},
         "expires_after": {"anchor": "last_active_at", "days": 1}}),
        ("upload", (), {"file": ("derived.json", b"{}", "application/json"), "purpose": "assistants",
         "expires_after": {"anchor": "created_at", "seconds": 86_400}}),
        ("retrieve_store", ("vs_candidate",), {}),
    ]


def test_adapter_maps_attribute_filters_and_search_results_without_custom_ranking():
    client = FakeVectorStoreClient()

    results = VectorStoreAdapter(client).search(
        "vs_current",
        "relationship between timing and narrative",
        attributes={"snapshot_id": "snap_current", "status": "published"},
        max_num_results=4,
    )

    assert results[0].record_id == "rec_1"
    assert results[0].text == "A concise orientation artifact."
    assert client.calls == [
        ("search", ("vs_current",), {
            "query": "relationship between timing and narrative",
            "filters": {"type": "and", "filters": [
                {"type": "eq", "key": "snapshot_id", "value": "snap_current"},
                {"type": "eq", "key": "status", "value": "published"},
            ]},
            "max_num_results": 4,
        })
    ]


def test_vector_store_metadata_is_string_only_but_file_attributes_keep_openai_scalar_values():
    client = FakeVectorStoreClient()
    adapter = VectorStoreAdapter(client)

    with pytest.raises(ValueError, match="metadata values must be strings"):
        adapter.create_store("Candidate", {"snapshot_id": True})

    adapter.attach_file(
        "vs_candidate", "file_orientation", {"rank": 2, "published": True, "scope": "2026"}
    )

    assert client.calls == [
        ("attach", ("vs_candidate",), {
            "file_id": "file_orientation",
            "attributes": {"rank": 2, "published": True, "scope": "2026"},
        })
    ]


def test_detach_only_removes_the_vector_store_attachment_not_the_openai_file():
    client = FakeVectorStoreClient()

    VectorStoreAdapter(client).detach_file("vs_old", "file_orientation")

    assert client.calls == [("detach", ("file_orientation",), {"vector_store_id": "vs_old"})]
    assert not hasattr(client, "files_delete_calls")


def test_existing_file_rejection_reports_the_documented_candidate_fallback_boundary():
    client = FakeVectorStoreClient()
    client.attachment_error = RuntimeError("file cannot be attached to multiple vector stores")

    outcome = VectorStoreAdapter(client).attach_existing_file("vs_candidate", "file_raw", {})

    assert outcome.status == "unsupported"
    assert outcome.file_id == "file_raw"
    assert "multiple vector stores" in outcome.reason


def test_only_explicit_multi_store_conflicts_become_unsupported_outcomes():
    client = FakeVectorStoreClient()
    client.attachment_error = RuntimeError("File cannot be found in this vector store")

    with pytest.raises(RuntimeError, match="cannot be found"):
        VectorStoreAdapter(client).attach_existing_file("vs_candidate", "file_raw", {})


def test_terminal_attachment_and_batch_failures_preserve_openai_diagnostics():
    client = FakeVectorStoreClient()
    client.attachment_response = SimpleNamespace(
        id="vsf_candidate",
        status="cancelled",
        last_error=SimpleNamespace(code="cancelled", message="Attachment was cancelled."),
    )
    client.retrieved_batch_response = SimpleNamespace(
        id="vsfb_candidate",
        status="failed",
        file_counts={"completed": 1, "failed": 1, "cancelled": 0},
        last_error=SimpleNamespace(code="vector_store_file_failed", message="One file failed."),
    )
    adapter = VectorStoreAdapter(client)

    attachment = adapter.attach_file("vs_candidate", "file_orientation", {})
    batch = adapter.batch_status("vs_candidate", "vsfb_candidate")

    assert attachment.status == "cancelled"
    assert attachment.last_error.code == "cancelled"
    assert attachment.last_error.message == "Attachment was cancelled."
    assert batch.status == "failed"
    assert batch.file_counts == {"completed": 1, "failed": 1, "cancelled": 0}
    assert batch.last_error.code == "vector_store_file_failed"
    assert batch.last_error.message == "One file failed."


def test_live_preflight_remains_disabled_unless_explicitly_approved(tmp_path):
    config = load_config({"OPENAI_API_KEY": "test-key"}, tmp_path / ".env")

    with pytest.raises(ConfigError, match="PHASE3_VECTOR_STORE_PREFLIGHT"):
        config.require_vector_store_preflight()

    approved = load_config(
        {"OPENAI_API_KEY": "test-key", "PHASE3_VECTOR_STORE_PREFLIGHT": "disposable-approved"},
        tmp_path / ".env",
    )
    approved.require_vector_store_preflight()

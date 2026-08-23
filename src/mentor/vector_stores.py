"""Small, fakeable boundary around OpenAI vector-store operations."""

from dataclasses import dataclass
from typing import Any, Mapping


MetadataValue = str
AttributeValue = str | int | float | bool


@dataclass(frozen=True)
class VectorStoreLastError:
    code: str | None
    message: str | None


@dataclass(frozen=True)
class VectorStore:
    store_id: str
    status: str | None


@dataclass(frozen=True)
class VectorStoreFile:
    file_id: str
    attachment_id: str
    status: str | None
    last_error: VectorStoreLastError | None


@dataclass(frozen=True)
class VectorStoreBatch:
    batch_id: str
    status: str | None
    file_counts: dict[str, int]
    last_error: VectorStoreLastError | None


@dataclass(frozen=True)
class ExistingFileAttachment:
    file_id: str
    status: str
    reason: str = ""


@dataclass(frozen=True)
class VectorStoreSearchResult:
    file_id: str
    filename: str | None
    score: float | None
    attributes: dict[str, AttributeValue]
    text: str

    @property
    def record_id(self) -> str | None:
        value = self.attributes.get("record_id")
        return value if isinstance(value, str) else None


class VectorStoreAdapter:
    """Use OpenAI-native search without adding local vector ranking."""

    def __init__(self, client: Any):
        self._client = client

    def create_store(self, name: str, metadata: Mapping[str, MetadataValue]) -> VectorStore:
        remote = self._client.vector_stores.create(
            name=name, metadata=_string_metadata(metadata)
        )
        return VectorStore(store_id=_required(remote, "id"), status=_optional(remote, "status"))

    def attach_file(
        self, vector_store_id: str, file_id: str, attributes: Mapping[str, AttributeValue]
    ) -> VectorStoreFile:
        remote = self._client.vector_stores.files.create(
            vector_store_id, file_id=file_id, attributes=_file_attributes(attributes)
        )
        return _vector_store_file(remote, file_id)

    def attach_existing_file(
        self, vector_store_id: str, file_id: str, attributes: Mapping[str, AttributeValue]
    ) -> ExistingFileAttachment:
        """Report a known capability rejection; never upload a fallback File here."""
        try:
            attached = self.attach_file(vector_store_id, file_id, attributes)
        except Exception as error:
            reason = str(error)
            if _is_multiple_store_rejection(reason):
                return ExistingFileAttachment(file_id=file_id, status="unsupported", reason=reason)
            raise
        return ExistingFileAttachment(file_id=file_id, status=attached.status or "unknown")

    def attachment_status(self, vector_store_id: str, file_id: str) -> VectorStoreFile:
        remote = self._client.vector_stores.files.retrieve(file_id, vector_store_id=vector_store_id)
        return _vector_store_file(remote, file_id)

    def create_batch(
        self, vector_store_id: str, file_ids: list[str], attributes: Mapping[str, AttributeValue]
    ) -> VectorStoreBatch:
        remote = self._client.vector_stores.file_batches.create(
            vector_store_id, file_ids=file_ids, attributes=_file_attributes(attributes)
        )
        return _vector_store_batch(remote)

    def batch_status(self, vector_store_id: str, batch_id: str) -> VectorStoreBatch:
        remote = self._client.vector_stores.file_batches.retrieve(
            batch_id, vector_store_id=vector_store_id
        )
        return _vector_store_batch(remote)

    def detach_file(self, vector_store_id: str, file_id: str) -> None:
        """Detach only; deleting an underlying OpenAI File is deliberately separate."""
        self._client.vector_stores.files.delete(file_id, vector_store_id=vector_store_id)

    def search(
        self,
        vector_store_id: str,
        query: str,
        *,
        attributes: Mapping[str, AttributeValue],
        max_num_results: int,
    ) -> list[VectorStoreSearchResult]:
        remote = self._client.vector_stores.search(
            vector_store_id,
            query=query,
            filters=_filters(attributes),
            max_num_results=max_num_results,
        )
        return [_search_result(result) for result in _value(remote, "data", [])]


def _filters(attributes: Mapping[str, AttributeValue]) -> dict[str, Any]:
    return {
        "type": "and",
        "filters": [
            {"type": "eq", "key": key, "value": value}
            for key, value in _file_attributes(attributes).items()
        ],
    }


def _vector_store_file(remote: Any, file_id: str) -> VectorStoreFile:
    return VectorStoreFile(
        file_id=file_id,
        attachment_id=_required(remote, "id"),
        status=_optional(remote, "status"),
        last_error=_last_error(remote),
    )


def _vector_store_batch(remote: Any) -> VectorStoreBatch:
    counts = _value(remote, "file_counts", {})
    if not isinstance(counts, Mapping):
        counts = vars(counts)
    return VectorStoreBatch(
        batch_id=_required(remote, "id"),
        status=_optional(remote, "status"),
        file_counts={str(key): int(value) for key, value in counts.items()},
        last_error=_last_error(remote),
    )


def _search_result(remote: Any) -> VectorStoreSearchResult:
    attributes = _value(remote, "attributes", {})
    if not isinstance(attributes, Mapping):
        raise ValueError("vector-store search result attributes must be a mapping")
    text = "\n".join(
        str(_value(content, "text", ""))
        for content in _value(remote, "content", [])
        if _value(content, "type") == "text"
    )
    return VectorStoreSearchResult(
        file_id=_required(remote, "file_id"),
        filename=_optional(remote, "filename"),
        score=_float_or_none(_value(remote, "score")),
        attributes=dict(attributes),
        text=text,
    )


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _required(value: Any, key: str) -> str:
    result = _value(value, key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"OpenAI vector-store response has no {key}")
    return result


def _optional(value: Any, key: str) -> str | None:
    result = _value(value, key)
    return result if isinstance(result, str) else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _string_metadata(metadata: Mapping[str, MetadataValue]) -> dict[str, str]:
    values = dict(metadata)
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
        raise ValueError("vector-store metadata values must be strings")
    return values


def _file_attributes(attributes: Mapping[str, AttributeValue]) -> dict[str, AttributeValue]:
    values = dict(attributes)
    if any(
        not isinstance(key, str) or not isinstance(value, str | int | float | bool)
        for key, value in values.items()
    ):
        raise ValueError("vector-store-file attributes must be strings, numbers, or booleans")
    return values


def _last_error(remote: Any) -> VectorStoreLastError | None:
    value = _value(remote, "last_error")
    if value is None:
        return None
    return VectorStoreLastError(code=_optional(value, "code"), message=_optional(value, "message"))


def _is_multiple_store_rejection(reason: str) -> bool:
    normalized = " ".join(reason.casefold().split())
    return any(
        phrase in normalized
        for phrase in (
            "cannot be attached to multiple vector stores",
            "already attached to another vector store",
            "already attached to a different vector store",
            "already associated with another vector store",
        )
    )

# Phase 3 vector-store capability preflight

**State: PRE-FLIGHT PASSED — 2026-08-23.** Theo authorized one guarded,
disposable-data-only probe. It contacted no existing project/Jacob resource and
uploaded no corpus content.

## Local boundary verified

The project uses `openai` 2.54.0 (`openai>=2,<3`). The local adapter follows
the currently installed SDK signatures for:

- `vector_stores.create(name=..., metadata=...)`;
- `vector_stores.files.create(vector_store_id, file_id=..., attributes=...)`;
- `vector_stores.file_batches.create(vector_store_id, file_ids=...,
  attributes=...)` and `retrieve(...)`;
- `vector_stores.search(vector_store_id, query=..., filters=...,
  max_num_results=...)`; and
- `vector_stores.files.delete(file_id, vector_store_id=...)` for detachment
  only. It never deletes the underlying OpenAI File.

The adapter exposes caller-controlled status retrieval; it does not poll. Its
tests use fakes only. They cover status mapping, attribute-filter mapping,
search-result mapping, batch status retrieval, detachment, and a reported
same-File/multiple-vector-store rejection. A rejection is a capability outcome,
not permission to silently upload, replace, or delete a File.

The local adapter classifies only explicit multi-store attachment-conflict
wording as that capability outcome. Confirmation of the live SDK/API's exact
typed error shape remains deferred because this run observed the supported
path; unrelated attachment errors continue to propagate unchanged.

## Official references

- [Vector Store Search](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/search)
- [Create Vector Store File](https://developers.openai.com/api/reference/cli/resources/vector_stores/subresources/files/methods/create)
- [Delete Vector Store File (detach)](https://developers.openai.com/api/reference/python/resources/vector_stores/subresources/files/methods/delete)
- [Vector Store File Batches](https://developers.openai.com/api/reference/cli/resources/vector_stores/subresources/file_batches)

## Completed preflight protocol and future rerun guard

Theo explicitly authorized and the project performed the disposable-data-only
probe described here. This project's observed result is recorded below. The
probe used an otherwise unused vector store and verified the current API/SDK
supports:

1. creating a disposable store and attaching a disposable File;
2. attaching the same File to a second disposable candidate store, or clearly
   reporting that unsupported outcome;
3. caller-controlled batch status retrieval and vector-store search attribute filters; and
4. detaching from the disposable store without deleting the underlying File.

The run recorded the SDK version, statuses, filter/search result, and cleanup
result locally without retaining credentials or resource IDs. Do not use Jacob
corpus content, existing Jacob Files, or the Jacob vector store for a
preflight. If future SDK/API capability drift needs confirmation, Theo must
explicitly authorize a fresh disposable preflight before it runs. If that probe
contradicts the approved raw/derived snapshot design, stop Phase 3 work and
report it.

`PHASE3_VECTOR_STORE_PREFLIGHT=disposable-approved` is an additional local
guard, not a substitute for Theo's explicit approval. No runner is wired in
this task, so setting it alone cannot create a remote resource.

## Authorized disposable preflight result

The preflight used `openai` 2.54.0 and one synthetic text marker with an
opaque, disposable run tag. No OpenAI resource IDs, API key, or private content
are retained here.

| Observation | Result |
|---|---|
| Disposable resources | Two newly created, positively tagged vector stores and one synthetic File only. |
| Direct attachment | Started `in_progress`; caller-controlled retrieval reached `completed`. |
| Same File in candidate store | The same synthetic File was attached to the second store through a file batch. The batch started `in_progress` and reached `completed`; file counts were total 1, completed 1, failed 0, cancelled 0. |
| Attribute-filtered search | A `preflight` equality filter returned exactly one matching synthetic artifact. |
| Detachment | Deleting the first store attachment returned `deleted: true`. The underlying synthetic OpenAI File was deliberately not deleted. |
| Cleanup | Both stores created by this run returned `deleted: true`; no existing store, Jacob File, Jacob vector store, or underlying File was deleted. |
| Cost | The SDK responses exposed no cost value. No cost is claimed or estimated from this probe. |

This is compatible with the Phase 3 immutable raw/derived store-pair design:
the same File can be attached to current and candidate stores, search filters
work, and vector-store-file detachment does not require deleting the File. The
documented byte-identical candidate-upload fallback is not needed for this API
version, but remains a future compatibility fallback if the observed capability
changes.

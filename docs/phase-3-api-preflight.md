# Phase 3 vector-store capability preflight

**State: PRE-FLIGHT PENDING.** No live preflight has been run. The current
environment has no configured `OPENAI_API_KEY`; this task did not contact
OpenAI, inspect existing remote resources, or upload any file.

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

The adapter's tests use fakes only. They cover status mapping, attribute-filter
mapping, search-result mapping, batch polling, detachment, and a reported
same-File/multiple-vector-store rejection. A rejection is a capability outcome,
not permission to silently upload, replace, or delete a File.

## Official references

- [Vector Store Search](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/search)
- [Create Vector Store File](https://developers.openai.com/api/reference/cli/resources/vector_stores/subresources/files/methods/create)
- [Vector Store File Batches](https://developers.openai.com/api/reference/cli/resources/vector_stores/subresources/file_batches)

## Required live preflight before real compilation

After Theo explicitly approves it and configures credentials, run a small,
disposable-data-only probe against an otherwise unused vector store. It must
verify the current API/SDK supports:

1. creating a disposable store and attaching a disposable File;
2. attaching the same File to a second disposable candidate store, or clearly
   reporting that unsupported outcome;
3. batch status progression and vector-store search attribute filters; and
4. detaching from the disposable store without deleting the underlying File.

Record the SDK version, calls, identifiers, statuses, filter/search result, and
cleanup result locally. If the same-File behavior or any other capability
contradicts the approved raw/derived snapshot design, stop Phase 3 work and
report it. Do not use Jacob corpus content, existing Jacob Files, or the Jacob
vector store for this preflight.

`PHASE3_VECTOR_STORE_PREFLIGHT=disposable-approved` is an additional local
guard, not a substitute for Theo's explicit approval. No runner is wired in
this task, so setting it alone cannot create a remote resource.

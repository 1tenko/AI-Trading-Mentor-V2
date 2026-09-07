import assert from "node:assert/strict";
import test from "node:test";

import { pendingAttachmentView } from "../src/mentor/static/attachment_state.js";

const dataset = { id: "dataset-pending", original_name: "backtest.xlsx" };

test("an unresolved spreadsheet remains visible and removable in its conversation", () => {
  const view = pendingAttachmentView(7, { threadId: 7, dataset }, undefined);

  assert.deepEqual(view, { dataset, state: "Needs attention" });
});

test("a ready unsent spreadsheet remains visible in its conversation", () => {
  const view = pendingAttachmentView(7, undefined, { threadId: 7, dataset });

  assert.deepEqual(view, { dataset, state: "Ready" });
});

test("attachment state never leaks into another or unopened conversation", () => {
  const pending = { threadId: 7, dataset };

  assert.equal(pendingAttachmentView(8, pending, undefined), undefined);
  assert.equal(pendingAttachmentView(undefined, pending, undefined), undefined);
});

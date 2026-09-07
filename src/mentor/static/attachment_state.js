export function pendingAttachmentView(activeThreadId, pendingAttachment, pendingMessageAttachment) {
  if (!Number.isInteger(activeThreadId)) return undefined;
  if (pendingAttachment?.threadId === activeThreadId) {
    return { dataset: pendingAttachment.dataset, state: "Needs attention" };
  }
  if (pendingMessageAttachment?.threadId === activeThreadId) {
    return { dataset: pendingMessageAttachment.dataset, state: "Ready" };
  }
  return undefined;
}

import { marked } from "/vendor/marked.esm.js";

const threads = document.querySelector("#threads");
const messages = document.querySelector("#messages");
const form = document.querySelector("#question-form");
const question = document.querySelector("#question");
const status = document.querySelector("#status");
const send = form.querySelector("button[type=submit]");
const effort = document.querySelector("#reasoning-effort");
const mode = document.querySelector("#reasoning-mode");
const researchDepth = document.querySelector("#research-depth");
const dataWorkspace = document.querySelector("#data-workspace");
const dataStatus = document.querySelector("#data-status");
const datasetSelect = document.querySelector("#dataset-select");
const datasetInspection = document.querySelector("#dataset-inspection");
const dataScope = document.querySelector("#data-scope");
const attachmentFile = document.querySelector("#attachment-file");
const attachmentChip = document.querySelector("#attachment-chip");
const attachmentName = document.querySelector("#attachment-name");
const attachmentMeta = document.querySelector("#attachment-meta");
const attachmentClarifications = document.querySelector("#attachment-clarifications");
const notesConsent = document.querySelector("#notes-consent");
const theme = document.querySelector("#theme");
const SETTINGS_KEY = "trading-mentor-evaluation-settings";
const ACTIVE_THREAD_KEY = "trading-mentor-active-thread";
const THEME_KEY = "trading-mentor-theme";
const CONTINUE_PROMPT = "Continue the previous response from where it stopped. Do not repeat completed material.";
let activeThreadId;
let activeDatasetScope;
let pendingAttachment;
let pendingMessageAttachment;
let pendingQualitativeQuestion;
let conversationLoadToken = 0;

function clearPendingChatInteraction() {
  pendingAttachment = undefined;
  pendingMessageAttachment = undefined;
  pendingQualitativeQuestion = undefined;
  attachmentClarifications.replaceChildren();
  attachmentClarifications.hidden = true;
  notesConsent.replaceChildren();
  notesConsent.hidden = true;
}

function showEmpty() {
  messages.replaceChildren();
  const empty = document.createElement("p");
  empty.className = "empty";
  empty.textContent = "How can I help with your trading today?";
  messages.append(empty);
}

function showMessage(label, text, attachment = undefined) {
  messages.querySelector(".empty")?.remove();
  const message = document.createElement("section");
  message.className = `message message--${label.toLowerCase()}`;
  const heading = document.createElement("strong");
  heading.className = "message-label";
  heading.textContent = label;
  const content = document.createElement("div");
  content.className = label === "Mentor" ? "message-content markdown" : "message-content";
  if (label === "Mentor") renderMarkdown(content, text);
  else content.textContent = text;
  message.append(heading);
  if (attachment) message.append(renderMessageAttachment(attachment));
  message.append(content);
  messages.append(message);
  return { message, heading, content };
}

function renderMarkdown(target, text) {
  target.innerHTML = DOMPurify.sanitize(marked.parse(text, { breaks: true, gfm: true }), { USE_PROFILES: { html: true } });
  target.querySelectorAll("table").forEach((table) => {
    const scroll = document.createElement("div");
    scroll.className = "markdown-table-scroll";
    table.replaceWith(scroll);
    scroll.append(table);
  });
  target.querySelectorAll("a").forEach((link) => {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  });
}

function showEvidence(evidence, citations) {
  const cited = new Set(citations.map((citation) => citation.file_id));
  const items = [];
  const seen = new Set();
  evidence.forEach((item) => {
    const key = `${item.file_id}:${item.excerpt}`;
    if (seen.has(key)) return;
    seen.add(key);
    items.push(item);
  });
  if (!items.length && !citations.length) return;
  const block = document.createElement("details");
  block.className = "evidence";
  const summary = document.createElement("summary");
  summary.textContent = `${citations.length} cited source${citations.length === 1 ? "" : "s"} · ${items.length} retrieved passage${items.length === 1 ? "" : "s"}`;
  const content = document.createElement("div");
  content.className = "evidence-content";
  citations.forEach((citation) => {
    const passages = items.filter((item) => item.file_id === citation.file_id);
    content.append(citedSource(citation, passages));
  });
  const additionalItems = items.filter((item) => !cited.has(item.file_id));
  if (additionalItems.length) {
    const additional = document.createElement("div");
    additional.className = "evidence-additional";
    additional.hidden = true;
    additionalItems.forEach((item) => additional.append(evidenceItem(item)));
    const remaining = additionalItems.length;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.textContent = `Show ${remaining} additional research result${remaining === 1 ? "" : "s"}`;
    toggle.addEventListener("click", () => {
      additional.hidden = !additional.hidden;
      toggle.textContent = additional.hidden
        ? `Show ${remaining} additional research result${remaining === 1 ? "" : "s"}`
        : "Hide additional research results";
    });
    const label = document.createElement("strong");
    label.textContent = "Additional research results";
    content.append(label, toggle, additional);
  }
  block.append(summary, content);
  messages.append(block);
}

function citedSource(citation, passages) {
  const entry = document.createElement("section");
  entry.className = "evidence-item";
  const heading = document.createElement("strong");
  heading.textContent = formatEvidenceDate(passages[0] || {}) || citation.filename || "Source";
  const badge = document.createElement("small");
  badge.textContent = "Cited source";
  entry.append(heading, badge);
  if (passages.length) {
    const label = document.createElement("span");
    label.textContent = "Retrieved passages from this source:";
    entry.append(label);
    passages.forEach((passage) => entry.append(evidencePassage(passage)));
  } else {
    const empty = document.createElement("span");
    empty.textContent = "No retrieved passages were returned for this cited source.";
    entry.append(empty);
  }
  entry.append(sourceLink(citation.file_id));
  return entry;
}

function evidenceItem(item) {
  const entry = document.createElement("div");
  entry.className = "evidence-item";
  const heading = document.createElement("strong");
  heading.textContent = formatEvidenceDate(item) || item.filename || "Source";
  const label = document.createElement("small");
  label.textContent = "Retrieved passage";
  entry.append(heading, label, evidencePassage(item), sourceLink(item.file_id));
  return entry;
}

function evidencePassage(item) {
  const passage = document.createElement("div");
  passage.className = "evidence-passage";
  const timestamp = formatEvidenceTimestamp(item.excerpt || "");
  const excerpt = document.createElement("span");
  excerpt.className = "evidence-excerpt";
  excerpt.textContent = shortOriginalExcerpt(item.excerpt || "");
  if (timestamp) {
    const time = document.createElement("small");
    time.textContent = timestamp;
    passage.append(time);
  }
  passage.append(excerpt);
  return passage;
}

function sourceLink(fileId) {
  const link = document.createElement("a");
  link.href = `/api/sources/${encodeURIComponent(fileId)}`;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "Open full transcript";
  return link;
}

function formatEvidenceTimestamp(excerpt) {
  const match = excerpt.match(/\[(\d+(?:\.\d+)?)\s*(?:-->|→)\s*(\d+(?:\.\d+)?)\]/);
  return match ? `${formatSeconds(Number(match[1]))}–${formatSeconds(Number(match[2]))}` : "";
}

function formatSeconds(value) {
  const seconds = Math.max(0, Math.floor(value));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function formatEvidenceDate(item) {
  const path = item.metadata?.relative_path || "";
  const match = path.match(/\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(st|nd|rd|th)?\b/i);
  if (!match || !item.year) return "";
  const month = `${match[1][0].toUpperCase()}${match[1].slice(1).toLowerCase()}`;
  return `${month} ${match[2]}${match[3] || ordinalSuffix(Number(match[2]))} (${item.year})`;
}

function ordinalSuffix(day) {
  if (day % 100 >= 11 && day % 100 <= 13) return "th";
  return ({ 1: "st", 2: "nd", 3: "rd" })[day % 10] || "th";
}

function shortOriginalExcerpt(excerpt) {
  const withoutTimestamp = excerpt.replace(/^\s*\[\d+(?:\.\d+)?\s*(?:-->|→)\s*\d+(?:\.\d+)?\]\s*/, "").trim();
  const limit = 700;
  return withoutTimestamp.length > limit ? `${withoutTimestamp.slice(0, limit)}…` : withoutTimestamp || "No retrieved excerpt was returned for this citation.";
}

function showDiagnostics(diagnostics) {
  if (!diagnostics) return;
  const latency = Number.isFinite(diagnostics.latency_ms) ? `${(diagnostics.latency_ms / 1000).toFixed(1)}s` : "Unavailable";
  const research = diagnostics.requested_research_depth && diagnostics.effective_research_depth
    ? `${diagnostics.requested_research_depth} requested · ${diagnostics.effective_research_depth} used`
    : "Unavailable";
  const searches = Number.isFinite(diagnostics.file_search_calls)
    ? `${diagnostics.file_search_calls} calls · ${diagnostics.file_search_queries?.length ?? 0} queries · ${diagnostics.returned_evidence_count ?? 0} results · ${diagnostics.cited_evidence_count ?? 0} cited`
    : "Unavailable";
  const analysisCalls = diagnostics.analysis_calls || { requested: 0, executed: 0, rejected: 0 };
  const analysisContext = `${(diagnostics.deterministic_result_chars || 0).toLocaleString()} / 32,000 chars`;
  const rows = [
    ["Model", diagnostics.model || "Unavailable"],
    ["Reasoning", diagnostics.reasoning_effort && diagnostics.reasoning_mode ? `${diagnostics.reasoning_effort} / ${diagnostics.reasoning_mode}` : "Unavailable"],
    ["Research depth", research],
    ["Research", searches],
    ["Status", diagnostics.status || "Unavailable"],
    ["Latency", latency],
    ["Tokens", `${diagnostics.input_tokens ?? "—"} input · ${diagnostics.output_tokens ?? "—"} output · ${diagnostics.reasoning_tokens ?? "—"} reasoning`],
    ["Text-token estimate", diagnostics.estimated_text_cost_usd == null ? "Unavailable" : `$${diagnostics.estimated_text_cost_usd.toFixed(4)} (excludes File Search fees)`],
    ["Cached input", diagnostics.cached_input_tokens == null ? "Unavailable" : diagnostics.cached_input_tokens.toLocaleString()],
    ["Cache write", diagnostics.cache_write_tokens == null ? "Unavailable" : diagnostics.cache_write_tokens.toLocaleString()],
    ["File Search calls", `${diagnostics.file_search_calls || 0} · ${diagnostics.known_file_search_call_cost_usd == null ? "cost unavailable" : `$${diagnostics.known_file_search_call_cost_usd.toFixed(4)} known call cost`}`],
    ["File Search/platform cost", diagnostics.file_search_cost_status || "Unknown"],
    ["Analysis calls", `${analysisCalls.requested} requested · ${analysisCalls.executed} executed · ${analysisCalls.rejected} rejected`],
    ["Analysis operations", diagnostics.analysis_operations?.join(", ") || "None"],
    ["Deterministic result context", analysisContext],
    ["Qualitative calls", String(diagnostics.qualitative_calls || 0)],
    ["Analysis batch", diagnostics.analysis_batch_status || "Not requested"],
  ];
  rows.push(["Native compaction", diagnostics.native_compaction_applied
    ? "Applied for future model replay; included in this response usage."
    : "Not applied on this turn"]);
  const block = document.createElement("details");
  block.className = "diagnostics";
  const summary = document.createElement("summary");
  summary.textContent = "Evaluation diagnostics";
  const content = document.createElement("div");
  content.className = "diagnostics-content";
  const list = document.createElement("dl");
  rows.forEach(([name, value]) => {
    const term = document.createElement("dt");
    term.textContent = name;
    const detail = document.createElement("dd");
    detail.textContent = value;
    list.append(term, detail);
  });
  content.append(list);
  block.append(summary, content);
  messages.append(block);
}

function showProfileUpdate(update) {
  if (!update?.kind) return;
  const block = document.createElement("aside");
  block.className = "profile-update";
  block.setAttribute("aria-live", "polite");
  if (update.kind === "proposed") {
    block.append("Profile update needs confirmation. It is not active memory yet. ");
    const review = document.createElement("button");
    review.type = "button";
    review.textContent = "Review in Trader Profile";
    review.addEventListener("click", () => window.location.assign("/profile"));
    block.append(review);
  } else {
    block.textContent = update.kind === "saved" ? "Saved to Trader Profile." : "Removed from Trader Profile.";
  }
  messages.append(block);
}

function renderMessageAttachment(attachment) {
  const card = document.createElement("section");
  card.className = "message-attachment";
  const name = document.createElement("strong");
  name.textContent = attachment.original_name;
  const meta = document.createElement("small");
  meta.textContent = `${attachment.source_row_count} rows`;
  card.append(name, meta);
  return card;
}

function restorePendingMessageAttachment(attachment) {
  if (!attachment || attachment.threadId !== activeThreadId) return;
  pendingMessageAttachment = attachment;
  showAttachmentState(attachment.dataset, "Ready");
}

function completePendingMessageAttachment(attachment) {
  if (pendingMessageAttachment === attachment) pendingMessageAttachment = undefined;
  attachmentChip.hidden = true;
}

function showIncomplete(answer, mentor) {
  mentor.heading.textContent = "Mentor — incomplete";
  const block = document.createElement("section");
  block.className = "incomplete";
  const reason = answer.incomplete_reason === "max_output_tokens" ? "The response reached its output limit." : "The response did not finish.";
  block.append(`${reason} This is partial, not a complete answer.`);
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Continue";
  button.addEventListener("click", () => sendMessage(CONTINUE_PROMPT, false));
  block.append(document.createElement("br"), button);
  messages.append(block);
}

function showStreamError(mentor, message, retryText) {
  mentor.heading.textContent = "Mentor — unavailable";
  mentor.content.classList.remove("markdown");
  mentor.content.replaceChildren(document.createTextNode(message));
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "Retry";
  retry.addEventListener("click", () => sendMessage(retryText, false));
  mentor.content.append(document.createElement("br"), retry);
}

function evaluation() {
  return { reasoning_effort: effort.value, reasoning_mode: mode.value, research_depth: researchDepth.value };
}

function restoreEvaluation() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY));
    if (["high", "xhigh", "max"].includes(saved?.reasoning_effort)) effort.value = saved.reasoning_effort;
    if (["standard", "pro"].includes(saved?.reasoning_mode)) mode.value = saved.reasoning_mode;
    if (["auto", "normal", "deep", "exhaustive"].includes(saved?.research_depth)) researchDepth.value = saved.research_depth;
  } catch { /* The defaults remain safe when local settings are unavailable. */ }
}

function persistEvaluation() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(evaluation()));
}

function applyTheme(preference) {
  const selected = ["dark", "light", "system"].includes(preference) ? preference : "dark";
  const resolved = selected === "system" && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : selected;
  document.documentElement.dataset.theme = resolved;
  theme.value = selected;
  localStorage.setItem(THEME_KEY, selected);
}

function conversationTitle(text) {
  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length > 56 ? `${compact.slice(0, 55)}…` : compact || "New conversation";
}

async function loadThreads() {
  const response = await fetch("/api/threads");
  const data = await response.json();
  threads.replaceChildren();
  data.threads.forEach((thread) => {
    const row = document.createElement("div");
    row.className = "thread-row";
    const button = document.createElement("button");
    button.className = "thread";
    button.type = "button";
    button.textContent = thread.title;
    button.setAttribute("aria-current", String(thread.id === activeThreadId));
    button.addEventListener("click", () => {
      activeThreadId = thread.id;
      localStorage.setItem(ACTIVE_THREAD_KEY, String(thread.id));
      status.textContent = `Conversation: ${thread.title}`;
      loadThread(thread.id).catch(() => { status.textContent = "Could not restore this conversation."; });
      loadThreads().catch(() => { status.textContent = "Could not refresh conversations."; });
    });
    const remove = document.createElement("button");
    remove.className = "thread-delete";
    remove.type = "button";
    remove.textContent = "Delete";
    remove.setAttribute("aria-label", `Delete conversation: ${thread.title}`);
    remove.addEventListener("click", () => deleteThread(thread).catch((error) => { status.textContent = error.message; }));
    row.append(button, remove);
    threads.append(row);
  });
}

async function loadThread(threadId) {
  clearPendingChatInteraction();
  const requestToken = ++conversationLoadToken;
  const response = await fetch(`/api/threads/${threadId}`);
  if (!response.ok) throw new Error("Could not restore this conversation.");
  const thread = await response.json();
  if (requestToken !== conversationLoadToken) return;
  activeThreadId = thread.id;
  localStorage.setItem(ACTIVE_THREAD_KEY, String(thread.id));
  renderTimeline(thread.turns);
  activeDatasetScope = thread.dataset_scope;
  updateDatasetScope();
  status.textContent = `Conversation: ${thread.title}`;
}

function renderTimeline(turns) {
  messages.replaceChildren();
  if (!turns.length) {
    showEmpty();
    return;
  }
  turns.forEach((turn) => {
    showMessage("Theo", turn.user_text, turn.attachment);
    if (!turn.answer_markdown && !turn.incomplete_reason) return;
    const mentor = showMessage("Mentor", turn.answer_markdown || "");
    showProfileUpdate(turn.profile_update);
    showEvidence(turn.evidence || [], turn.citations || []);
    showDiagnostics(turn.diagnostics);
    if (turn.incomplete_reason) showIncomplete(turn, mentor);
  });
}

async function deleteThread(thread) {
  if (!window.confirm(`Permanently delete “${thread.title}”? This only removes this local conversation.`)) return;
  const response = await fetch(`/api/threads/${thread.id}`, { method: "DELETE" });
  if (!response.ok) throw new Error("Could not delete this conversation.");
  if (activeThreadId === thread.id) {
    clearPendingChatInteraction();
    activeThreadId = undefined;
    localStorage.removeItem(ACTIVE_THREAD_KEY);
    showEmpty();
    status.textContent = "Conversation deleted.";
  }
  await loadThreads();
}

async function createThread(title = "New conversation") {
  clearPendingChatInteraction();
  conversationLoadToken += 1;
  const response = await fetch("/api/threads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
  if (!response.ok) throw new Error("Could not create a conversation.");
  const thread = await response.json();
  activeThreadId = thread.id;
  activeDatasetScope = undefined;
  updateDatasetScope();
  localStorage.setItem(ACTIVE_THREAD_KEY, String(thread.id));
  showEmpty();
  await loadThreads();
}

async function loadDatasets() {
  const response = await fetch("/api/datasets");
  if (!response.ok) throw new Error("Could not load local datasets.");
  const data = await response.json();
  const selected = datasetSelect.value;
  datasetSelect.replaceChildren(new Option("Choose imported data", ""));
  data.datasets.forEach((dataset) => datasetSelect.add(new Option(`${dataset.original_name} · ${dataset.source_row_count} rows`, dataset.id)));
  if ([...datasetSelect.options].some((option) => option.value === selected)) datasetSelect.value = selected;
}

function updateDatasetScope() {
  const scope = activeDatasetScope;
  if (scope && [...datasetSelect.options].some((option) => option.value === scope.dataset_id)) {
    datasetSelect.value = scope.dataset_id;
  }
  document.querySelector("#dataset-use").disabled = !activeThreadId || !datasetSelect.value;
  document.querySelector("#dataset-clear").disabled = !activeThreadId || !scope;
  dataScope.textContent = scope ? scope.original_name : "";
  attachmentChip.hidden = true;
  if (scope) dataStatus.textContent = `Using ${scope.original_name} in this conversation only.`;
}

async function inspectSelectedDataset() {
  const datasetId = datasetSelect.value;
  if (!datasetId) throw new Error("Choose a local dataset first.");
  const response = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}`);
  if (!response.ok) throw new Error((await response.json()).error || "Could not inspect this dataset.");
  const data = await response.json();
  renderDatasetInspection(data);
  dataStatus.textContent = `${data.dataset.original_name} is local. Confirm the mapping before using it in a conversation.`;
}

function renderDatasetInspection(data) {
  datasetInspection.replaceChildren();
  const intro = document.createElement("p");
  intro.className = "data-inspection-intro";
  intro.textContent = `${data.dataset.source_row_count} source rows. Headers and preview stay in this local browser workspace.`;
  const table = document.createElement("table");
  table.className = "data-mapping-table";
  const head = document.createElement("thead");
  head.innerHTML = "<tr><th>Column</th><th>Health</th><th>Meaning</th><th>Unit</th><th>Mentor access</th><th>Safe label</th><th>Share aggregate labels</th></tr>";
  const body = document.createElement("tbody");
  const suggestions = new Map(data.suggestions.map((suggestion) => [suggestion.column_ordinal, suggestion]));
  const entries = new Map((data.entries || []).map((entry) => [entry.column_ordinal, entry]));
  data.columns.forEach((column) => {
    const current = entries.get(column.ordinal) || suggestions.get(column.ordinal) || {};
    const row = document.createElement("tr");
    row.dataset.ordinal = String(column.ordinal);
    const role = selectControl([
      ["", "Not used"], ["trade_return", "Trade return"], ["trade_outcome", "Outcome"], ["trade_timestamp", "Timestamp"], ["session", "Session"], ["direction", "Direction"], ["mfe", "MFE"], ["mae", "MAE"], ["instrument", "Instrument"], ["setup", "Setup"],
    ], current.semantic_role || "", "mapping-role");
    const unit = selectControl([["", "—"], ["R", "R"], ["currency", "Currency"], ["points", "Points"], ["percentage", "Percentage"]], current.unit || "", "mapping-unit");
    const access = selectControl([["aggregates_only", "Aggregates only"], ["allow_row_values_when_analysing_notes", "Approved notes only"]], current.mentor_access || "aggregates_only", "mapping-access");
    const label = document.createElement("input");
    label.className = "mapping-label";
    label.maxLength = 80;
    label.placeholder = "Optional safe label";
    label.value = current.analysis_label || "";
    const modelDisclosure = document.createElement("input");
    modelDisclosure.type = "checkbox";
    modelDisclosure.className = "mapping-disclosure";
    modelDisclosure.checked = Boolean(current.aggregate_labels_allowed);
    modelDisclosure.disabled = !["categorical", "boolean"].includes(column.value_type);
    modelDisclosure.setAttribute("aria-label", `Share aggregate labels for ${column.original_header}`);
    row.append(
      cell(column.original_header),
      cell(`${column.valid_count} valid · ${column.blank_count} blank${column.invalid_count ? ` · ${column.invalid_count} invalid` : ""}`),
      cell(role), cell(unit), cell(access), cell(label), cell(modelDisclosure),
    );
    body.append(row);
  });
  table.append(head, body);
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.textContent = data.mapping?.status === "confirmed" ? "Confirm new mapping version" : "Confirm mapping";
  confirm.addEventListener("click", () => confirmDatasetMapping(data.dataset.id).catch((error) => { dataStatus.textContent = error.message; }));
  datasetInspection.append(intro, table, confirm);
  if (data.preview.length) datasetInspection.append(renderLocalPreview(data.preview));
}

function renderLocalPreview(preview) {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = "Local preview";
  const table = document.createElement("table");
  table.className = "data-mapping-table";
  const headers = Object.keys(preview[0]);
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headers.forEach((header) => headRow.append(cell(header)));
  head.append(headRow);
  const body = document.createElement("tbody");
  preview.forEach((row) => {
    const tr = document.createElement("tr");
    headers.forEach((header) => tr.append(cell(row[header] || "")));
    body.append(tr);
  });
  table.append(head, body);
  section.append(heading, table);
  return section;
}

function selectControl(options, value, className) {
  const select = document.createElement("select");
  select.className = className;
  options.forEach(([optionValue, text]) => select.add(new Option(text, optionValue, false, optionValue === value)));
  return select;
}

function cell(content) {
  const element = document.createElement("td");
  if (typeof content === "string") element.textContent = content;
  else element.append(content);
  return element;
}

async function confirmDatasetMapping(datasetId) {
  const entries = [...datasetInspection.querySelectorAll("tr[data-ordinal]")].map((row) => ({
    column_ordinal: Number(row.dataset.ordinal),
    semantic_role: row.querySelector(".mapping-role").value || null,
    unit: row.querySelector(".mapping-unit").value || null,
    mentor_access: row.querySelector(".mapping-access").value,
    analysis_label: row.querySelector(".mapping-label").value.trim() || null,
    model_disclosure: row.querySelector(".mapping-disclosure").checked,
  }));
  const response = await fetch(`/api/datasets/${encodeURIComponent(datasetId)}/mapping`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries }),
  });
  if (!response.ok) throw new Error((await response.json()).error || "Could not confirm this mapping.");
  dataStatus.textContent = "Mapping confirmed locally. You can now use this dataset in the current conversation.";
  await loadDatasets();
}

async function useSelectedDataset() {
  if (!activeThreadId) throw new Error("Open a conversation before selecting data.");
  const datasetId = datasetSelect.value;
  if (!datasetId) throw new Error("Choose a local dataset first.");
  const response = await fetch(`/api/threads/${activeThreadId}/dataset`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dataset_id: datasetId }),
  });
  if (!response.ok) throw new Error((await response.json()).error || "Could not select this dataset.");
  activeDatasetScope = (await response.json()).dataset_scope;
  updateDatasetScope();
}

async function clearDatasetScope() {
  if (!activeThreadId) return;
  const response = await fetch(`/api/threads/${activeThreadId}/dataset`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dataset_id: null }),
  });
  if (!response.ok) throw new Error("Could not clear this conversation’s data.");
  activeDatasetScope = undefined;
  updateDatasetScope();
  dataStatus.textContent = "Conversation data cleared. Other conversations were not changed.";
}

async function removeAttachment() {
  if (pendingMessageAttachment?.threadId === activeThreadId) {
    const response = await fetch(`/api/threads/${activeThreadId}/dataset`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_id: pendingMessageAttachment.previousDatasetId }),
    });
    if (!response.ok) throw new Error("Could not remove this pending spreadsheet.");
    activeDatasetScope = (await response.json()).dataset_scope;
    pendingMessageAttachment = undefined;
    attachmentFile.value = "";
    attachmentChip.hidden = true;
    updateDatasetScope();
    return;
  }
  if (pendingAttachment?.threadId === activeThreadId) {
    pendingAttachment = undefined;
    attachmentClarifications.hidden = true;
    attachmentFile.value = "";
    if (activeDatasetScope) updateDatasetScope();
    else attachmentChip.hidden = true;
    return;
  }
  await clearDatasetScope();
}

async function importDataset(event) {
  event.preventDefault();
  const file = document.querySelector("#dataset-file").files[0];
  if (!file) throw new Error("Choose a CSV or XLSX file.");
  dataStatus.textContent = "Importing locally…";
  const response = await fetch("/api/datasets/import", {
    method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Dataset-Filename": file.name }, body: file,
  });
  if (!response.ok) throw new Error((await response.json()).error || "Could not import this dataset.");
  const data = await response.json();
  await loadDatasets();
  datasetSelect.value = data.dataset.id;
  await inspectSelectedDataset();
}

function showAttachmentState(dataset, state) {
  attachmentChip.hidden = false;
  attachmentName.textContent = dataset.original_name;
  attachmentMeta.textContent = `${dataset.source_row_count} rows · ${state}`;
}

function showAttachmentIssue(data) {
  showAttachmentState(data.dataset, "Needs attention");
  attachmentClarifications.replaceChildren();
  attachmentClarifications.hidden = false;
  const text = document.createElement("p");
  text.textContent = data.message || "I couldn't prepare this spreadsheet. Try again or review the issue.";
  const review = document.createElement("button");
  review.type = "button";
  review.textContent = "Review issue";
  review.addEventListener("click", async () => {
    await loadDatasets();
    datasetSelect.value = data.dataset.id;
    await inspectSelectedDataset();
    if (dataWorkspace.hidden) openDataSettings();
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => removeAttachment().catch((error) => { status.textContent = error.message; }));
  attachmentClarifications.append(text, review, remove);
}

function showMappingClarifications(data, threadId) {
  attachmentClarifications.replaceChildren();
  attachmentClarifications.hidden = false;
  const text = document.createElement("p");
  text.textContent = `${data.dataset.original_name}: I can read this spreadsheet, but need one detail before I calculate returns.`;
  const form = document.createElement("form");
  data.clarifications.forEach((item) => {
    const label = document.createElement("label");
    label.textContent = `What does the ${item.header} column represent?`;
    const select = selectControl(
      item.role === "trade_timestamp"
        ? [["trade_timestamp", "Trade date/time"], ["", "Do not use it"]]
        : [["R", "R multiple"], ["currency", "Dollars"], ["points", "Points"], ["percentage", "Percentage"], ["", "Do not use it"]],
      "",
      "attachment-clarification",
    );
    select.dataset.ordinal = String(item.column_ordinal);
    select.dataset.role = item.role;
    label.append(select);
    form.append(label);
  });
  const continueButton = document.createElement("button");
  continueButton.type = "submit";
  continueButton.textContent = "Continue";
  form.append(continueButton);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    resolveAttachmentMapping(data, form, threadId).catch((error) => { status.textContent = error.message; });
  });
  attachmentClarifications.append(text, form);
}

async function resolveAttachmentMapping(data, form, threadId) {
  if (activeThreadId !== threadId) return;
  const entries = [...data.entries];
  form.querySelectorAll(".attachment-clarification").forEach((select) => {
    if (!select.value) return;
    entries.push({
      column_ordinal: Number(select.dataset.ordinal),
      semantic_role: select.dataset.role,
      unit: select.dataset.role === "trade_timestamp" ? null : select.value,
      source: "manual",
    });
  });
  const response = await fetch(`/api/datasets/${encodeURIComponent(data.dataset.id)}/mapping`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries }),
  });
  if (!response.ok) throw new Error((await response.json()).error || "Could not confirm this mapping.");
  if (activeThreadId !== threadId) return;
  const scope = await fetch(`/api/threads/${threadId}/dataset`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dataset_id: data.dataset.id }),
  });
  if (!scope.ok) throw new Error("Could not use this backtest in the conversation.");
  if (activeThreadId !== threadId) return;
  activeDatasetScope = (await scope.json()).dataset_scope;
  const previousDatasetId = pendingAttachment?.threadId === threadId ? pendingAttachment.previousDatasetId : null;
  pendingAttachment = undefined;
  attachmentClarifications.hidden = true;
  updateDatasetScope();
  pendingMessageAttachment = { threadId, dataset: data.dataset, previousDatasetId };
  showAttachmentState(data.dataset, "Ready");
}

async function attachDataset() {
  const file = attachmentFile.files[0];
  if (!file) return;
  if (!activeThreadId) await createThread("New conversation");
  const threadId = activeThreadId;
  const previousDatasetId = activeDatasetScope?.dataset_id ?? null;
  attachmentClarifications.hidden = true;
  let replace = false;
  if (activeDatasetScope) {
    replace = window.confirm("Replace the current backtest with this one for this conversation?");
    if (!replace) { updateDatasetScope(); return; }
  }
  if (!activeDatasetScope) showAttachmentState({ original_name: file.name, source_row_count: "" }, "Importing");
  const response = await fetch(`/api/threads/${threadId}/attachments`, {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Dataset-Filename": file.name,
      ...(replace ? { "X-Replace-Attachment": "true" } : {}),
    },
    body: file,
  });
  const data = await response.json();
  if (activeThreadId !== threadId) return;
  if (response.status === 422 && data.state === "needs_input") {
    pendingAttachment = { threadId, dataset: data.dataset, previousDatasetId };
    showAttachmentState(data.dataset, "Needs input");
    showMappingClarifications(data, threadId);
    return;
  }
  if (response.status === 422 && data.state === "error") {
    pendingAttachment = { threadId, dataset: data.dataset, previousDatasetId };
    showAttachmentIssue(data);
    return;
  }
  if (!response.ok) throw new Error("I couldn't import this backtest.");
  activeDatasetScope = data.dataset_scope;
  pendingMessageAttachment = { threadId, dataset: data.dataset, previousDatasetId };
  updateDatasetScope();
  showAttachmentState(data.dataset, "Ready");
  attachmentFile.value = "";
}

function showNotesConsent(questionText, threadId, attachment) {
  if (activeThreadId !== threadId) return;
  pendingQualitativeQuestion = questionText;
  notesConsent.replaceChildren();
  notesConsent.hidden = false;
  const message = document.createElement("p");
  message.textContent = "This spreadsheet contains written trade notes. Reading approved notes will send those values to the AI for this answer; the spreadsheet stays local.";
  const include = document.createElement("button");
  include.type = "button";
  include.textContent = "Include trade notes";
  include.addEventListener("click", () => {
    if (activeThreadId !== threadId) return;
    notesConsent.hidden = true;
    sendMessage(questionText, false, true, false, attachment);
  });
  const numbersOnly = document.createElement("button");
  numbersOnly.type = "button";
  numbersOnly.textContent = "Numbers only";
  numbersOnly.addEventListener("click", () => {
    if (activeThreadId !== threadId) return;
    notesConsent.hidden = true;
    sendMessage(questionText, false, false, true, attachment);
  });
  const choose = document.createElement("button");
  choose.type = "button";
  choose.textContent = "Choose fields";
  choose.addEventListener("click", () => openDataSettings());
  notesConsent.append(message, include, numbersOnly, choose);
}

function parseEventBuffer(buffer, onEvent) {
  const events = buffer.split("\n\n");
  events.slice(0, -1).forEach((event) => onEvent(JSON.parse(event.slice(5))));
  return events.at(-1);
}

async function sendMessage(text, showUser = true, includeApprovedNotes = false, numbersOnly = false, sentAttachment = undefined) {
  if (pendingAttachment?.threadId === activeThreadId) {
    status.textContent = "Resolve or remove the spreadsheet that needs attention before sending a message.";
    return;
  }
  if (!activeThreadId) await createThread(conversationTitle(text));
  const threadId = activeThreadId;
  const attachment = sentAttachment || (pendingMessageAttachment?.threadId === threadId ? pendingMessageAttachment : undefined);
  if (showUser) {
    showMessage("Theo", text, attachment?.dataset);
    if (attachment) {
      attachmentChip.hidden = true;
    }
  }
  status.textContent = "Thinking…";
  send.disabled = true;
  try {
    const response = await fetch(`/api/threads/${threadId}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text, evaluation: evaluation(), include_approved_notes: includeApprovedNotes, numbers_only: numbersOnly, attachment_dataset_id: attachment?.dataset.id ?? null }) });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || "The mentor is unavailable.");
    }
    if (response.headers.get("Content-Type")?.startsWith("text/event-stream")) {
      const mentor = showMessage("Mentor", "");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let answerText = "";
      let terminal = false;
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer = parseEventBuffer(buffer + decoder.decode(chunk.value, { stream: true }), (event) => {
          if (activeThreadId !== threadId) return;
          if (event.type === "delta") {
            answerText += event.text;
            renderMarkdown(mentor.content, answerText);
          }
          if (event.type === "complete" || event.type === "incomplete") {
            terminal = true;
            completePendingMessageAttachment(attachment);
            renderMarkdown(mentor.content, event.answer.text);
            showProfileUpdate(event.answer.profile_update);
            showEvidence(event.answer.evidence, event.answer.citations);
            showDiagnostics(event.answer.diagnostics);
            if (event.type === "incomplete") showIncomplete(event.answer, mentor);
          }
          if (event.type === "error") {
            terminal = true;
            restorePendingMessageAttachment(attachment);
            showStreamError(mentor, event.error || "The mentor request failed. Try again.", text);
          }
          if (event.type === "consent_required") {
            terminal = true;
            mentor.message.remove();
            showNotesConsent(text, threadId, attachment);
          }
        });
      }
      if (!terminal) {
        restorePendingMessageAttachment(attachment);
        showStreamError(mentor, "The mentor stream ended before returning a usable response. Try again.", text);
        status.textContent = "Mentor unavailable. You can retry.";
        return;
      }
      if (mentor.heading.textContent === "Mentor — unavailable") {
        restorePendingMessageAttachment(attachment);
        status.textContent = "Mentor unavailable. You can retry.";
        return;
      }
    } else {
      const answer = await response.json();
      completePendingMessageAttachment(attachment);
      const mentor = showMessage("Mentor", answer.text);
      showProfileUpdate(answer.profile_update);
      showEvidence(answer.evidence, answer.citations);
      showDiagnostics(answer.diagnostics);
      if (answer.incomplete_reason) showIncomplete(answer, mentor);
    }
    await loadThreads();
    status.textContent = "";
  } catch (error) {
    restorePendingMessageAttachment(attachment);
    status.textContent = error.message;
  } finally {
    send.disabled = false;
  }
}

function openDataSettings() {
  dataWorkspace.hidden = !dataWorkspace.hidden;
  if (!dataWorkspace.hidden) loadDatasets().catch((error) => { dataStatus.textContent = error.message; });
}

document.querySelector("#new-thread").addEventListener("click", () => createThread().catch((error) => { status.textContent = error.message; }));
document.querySelector("#data-settings").addEventListener("click", openDataSettings);
document.querySelector("#data-close").addEventListener("click", () => {
  dataWorkspace.hidden = true;
});
document.querySelector("#attachment-trigger").addEventListener("click", () => attachmentFile.click());
attachmentFile.addEventListener("change", () => attachDataset().catch(() => {
  if (activeDatasetScope) updateDatasetScope();
  else showAttachmentIssue({
    dataset: { original_name: attachmentFile.files[0]?.name || "Spreadsheet", source_row_count: "" },
    message: "I couldn't prepare this spreadsheet. Try again or review the issue.",
  });
}));
document.querySelector("#remove-attachment").addEventListener("click", () => removeAttachment().catch((error) => { status.textContent = error.message; }));
document.querySelector("#dataset-upload-form").addEventListener("submit", (event) => importDataset(event).catch((error) => { dataStatus.textContent = error.message; }));
document.querySelector("#dataset-inspect").addEventListener("click", () => inspectSelectedDataset().catch((error) => { dataStatus.textContent = error.message; }));
document.querySelector("#dataset-use").addEventListener("click", () => useSelectedDataset().catch((error) => { dataStatus.textContent = error.message; }));
document.querySelector("#dataset-clear").addEventListener("click", () => clearDatasetScope().catch((error) => { dataStatus.textContent = error.message; }));
datasetSelect.addEventListener("change", updateDatasetScope);
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = question.value.trim();
  if (!text) return;
  if (pendingAttachment?.threadId === activeThreadId) {
    status.textContent = "Resolve or remove the spreadsheet that needs attention before sending a message.";
    return;
  }
  question.value = "";
  await sendMessage(text);
});
restoreEvaluation();
applyTheme(localStorage.getItem(THEME_KEY) || "dark");
effort.addEventListener("change", persistEvaluation);
mode.addEventListener("change", persistEvaluation);
researchDepth.addEventListener("change", persistEvaluation);
theme.addEventListener("change", () => applyTheme(theme.value));
updateDatasetScope();
Promise.all([loadThreads(), loadDatasets()]).then(async ([,]) => {
  const savedThreadId = Number(localStorage.getItem(ACTIVE_THREAD_KEY));
  const firstThread = threads.querySelector(".thread");
  if (Number.isInteger(savedThreadId) && savedThreadId > 0) {
    try { await loadThread(savedThreadId); }
    catch { localStorage.removeItem(ACTIVE_THREAD_KEY); if (firstThread) firstThread.click(); else showEmpty(); }
  }
  else if (firstThread) firstThread.click();
  else showEmpty();
}).catch(() => { status.textContent = "Could not load conversations or local datasets."; });

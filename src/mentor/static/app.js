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
const inspector = document.querySelector("#inspector");
const inspectorToggle = document.querySelector("#inspector-toggle");
const inspectorClose = document.querySelector("#inspector-close");
const inspectorStatus = document.querySelector("#inspector-status");
const inspectorOverview = document.querySelector("#inspector-overview");
const inspectorDetail = document.querySelector("#inspector-detail");
const inspectorAudit = document.querySelector("#inspector-audit");
const chat = document.querySelector(".chat");
const SETTINGS_KEY = "trading-mentor-evaluation-settings";
const ACTIVE_THREAD_KEY = "trading-mentor-active-thread";
const CONTINUE_PROMPT = "Continue the previous response from where it stopped. Do not repeat completed material.";
let activeThreadId;

function inspectorSection(title, className = "") {
  const section = document.createElement("section");
  section.className = `inspector-section ${className}`.trim();
  const heading = document.createElement("h2");
  heading.textContent = title;
  section.append(heading);
  return section;
}

function inspectorValue(container, name, value) {
  const row = document.createElement("div");
  row.className = "inspector-value";
  const label = document.createElement("strong");
  label.textContent = name;
  const detail = document.createElement("span");
  detail.textContent = value == null || value === "" ? "Not recorded" : String(value);
  row.append(label, detail);
  container.append(row);
}

function inspectorList(container, values, empty = "None recorded.") {
  if (!values?.length) {
    const message = document.createElement("p");
    message.className = "inspector-empty";
    message.textContent = empty;
    container.append(message);
    return;
  }
  const list = document.createElement("ul");
  values.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = typeof value === "string" ? value : JSON.stringify(value);
    list.append(item);
  });
  container.append(list);
}

function inspectorTimestamp(start, end) {
  if (!Number.isInteger(start) || !Number.isInteger(end)) return "Range not recorded";
  return `${formatSeconds(start / 1000)}–${formatSeconds(end / 1000)}`;
}

function setInspectorOpen(open) {
  inspector.hidden = !open;
  chat.hidden = open;
  inspectorToggle.setAttribute("aria-expanded", String(open));
  if (open) {
    inspectorClose.focus();
    loadInspector().catch(() => showInspectorError("Could not load the assimilation inspector."));
  } else {
    inspectorToggle.focus();
  }
}

function showInspectorError(message) {
  inspectorStatus.textContent = message;
  inspectorOverview.replaceChildren();
  inspectorDetail.replaceChildren();
  inspectorAudit.replaceChildren();
}

function recordContentFields(data) {
  const content = data.content || {};
  if (data.family === "claim") return [["Subject", content.subject], ["Predicate", content.predicate], ["Object", content.object]];
  if (data.family === "relationship") return [["From", content.left], ["Relationship", content.relation], ["To", content.right]];
  if (data.family === "procedure_sequence_hierarchy") return [["Structure", content.kind], ["Ordered items", Array.isArray(content.terms) ? content.terms.join(", ") : null]];
  if (data.family === "evolution") return [
    ["Subject", content.subject], ["Earlier understanding", content.previous], ["Later understanding", content.current],
    ["Classification", content.classification], ["Evidence state", content.negative_evidence_state],
    ["Earlier observed years", Array.isArray(content.earlier_observed_years) ? content.earlier_observed_years.join(", ") : null],
    ["Later observed years", Array.isArray(content.later_observed_years) ? content.later_observed_years.join(", ") : null],
  ];
  if (data.family === "conflict_unresolved") return [
    ["Kind", content.kind], ["Subject", content.subject], ["Alternatives", Array.isArray(content.alternatives) ? content.alternatives.join(", ") : null],
    ["Reconciliation", content.reconciliation_state], ["Relevant scopes", Array.isArray(content.relevant_scopes) ? content.relevant_scopes.join(", ") : null],
    ["Conditions", Array.isArray(content.conditions) ? content.conditions.join(", ") : null],
    ["Open questions", Array.isArray(content.unresolved_questions) ? content.unresolved_questions.join(", ") : null],
  ];
  return [];
}

function dependencySummary(dependencies) {
  const counts = (dependencies || []).reduce((summary, dependency) => {
    const label = dependency.kind === "source_revision" ? "raw source revision" : dependency.kind === "derived_record" ? "derived record" : "other dependency";
    summary[label] = (summary[label] || 0) + 1;
    return summary;
  }, {});
  return Object.entries(counts).map(([label, count]) => `${count} ${label}${count === 1 ? "" : "s"}`);
}

async function inspectorJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error("Inspector request failed.");
  return response.json();
}

function showInspectorOverview(data) {
  inspectorOverview.replaceChildren();
  const overview = inspectorSection("Knowledge library");
  const collections = data.collections || [];
  inspectorValue(overview, "Collections", collections.map((item) => item.display_name).join(", ") || "None");
  inspectorValue(overview, "Published snapshot", data.current_snapshot?.snapshot_id || "None published");
  inspectorValue(overview, "Pending source changes", data.pending_source_changes?.length || "None");
  inspectorOverview.append(overview);

  const snapshots = inspectorSection("Snapshots");
  const rows = document.createElement("div");
  rows.className = "inspector-snapshots";
  (data.snapshots || []).forEach((snapshot) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `inspector-snapshot inspector-snapshot--${snapshot.status}`;
    button.textContent = `${snapshot.status === "published" ? "Current" : snapshot.status} · ${snapshot.snapshot_id}`;
    button.addEventListener("click", () => loadSnapshot(snapshot.snapshot_id).catch(() => showInspectorError("Could not load this snapshot.")));
    rows.append(button);
  });
  if (!rows.childElementCount) inspectorList(snapshots, [], "No compiled snapshots are available yet.");
  else snapshots.append(rows);
  inspectorOverview.append(snapshots);
}

function showSnapshot(data) {
  inspectorDetail.replaceChildren();
  const snapshot = data.snapshot;
  const section = inspectorSection("Snapshot status");
  inspectorValue(section, "Snapshot", snapshot.snapshot_id);
  inspectorValue(section, "Status", snapshot.status);
  inspectorValue(section, "Source revisions", snapshot.source_count);
  inspectorValue(section, "Coverage", `${data.coverage.processed} processed · ${data.coverage.failed} failed`);
  inspectorValue(section, "Compiler", data.compiler.model_version);
  inspectorValue(section, "Schema", data.compiler.schema_version);
  inspectorValue(section, "Candidate gate", data.candidate_gate?.status || "Not checked");
  const staleRecordIds = data.stale_record_ids;
  if (staleRecordIds?.length) inspectorValue(section, "Stale records", staleRecordIds.length);
  inspectorDetail.append(section);

  const records = inspectorSection("Derived records", "inspector--derived");
  const list = document.createElement("div");
  list.className = "inspector-records";
  (data.records || []).forEach((record) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "inspector-record";
    button.textContent = `Derived record · ${record.family} · ${record.provenance_label}${record.stale ? " · stale" : ""}`;
    button.addEventListener("click", () => loadRecord(snapshot.snapshot_id, record.record_id).catch(() => showInspectorError("Could not load this derived record.")));
    list.append(button);
  });
  if (!list.childElementCount) inspectorList(records, [], "This snapshot has no derived records.");
  else records.append(list);
  inspectorDetail.append(records);
}

function showRecord(data) {
  inspectorDetail.replaceChildren();
  const record = inspectorSection("Derived record", "inspector--derived");
  inspectorValue(record, "Family", data.family);
  inspectorValue(record, "Classification", data.provenance_label);
  inspectorValue(record, "Evidence state", data.evidence_state);
  inspectorValue(record, "Validation", data.validation_state);
  inspectorValue(record, "Lifecycle", `${data.lifecycle_state}${data.stale ? " · stale" : ""}`);
  inspectorValue(record, "Qualification", data.qualification);
  recordContentFields(data).forEach(([name, value]) => inspectorValue(record, name, value));
  inspectorDetail.append(record);

  const anchors = inspectorSection("Raw source anchors");
  (data.anchors || []).forEach((anchor) => {
    const item = document.createElement("article");
    item.className = "inspector-anchor";
    inspectorValue(item, "Raw source anchor", `${anchor.author || "Unknown author"} · ${anchor.course || "Unknown course"}`);
    inspectorValue(item, "Lesson", anchor.lesson_title || anchor.filename);
    inspectorValue(item, "Year", anchor.year);
    inspectorValue(item, "Timestamp", inspectorTimestamp(anchor.timestamp_start_ms, anchor.timestamp_end_ms));
    anchors.append(item);
  });
  if (!data.anchors?.length) inspectorList(anchors, [], "No safe raw-source anchors are available for this record.");
  inspectorDetail.append(anchors);

  const context = inspectorSection("Relationships, evolution, and uncertainty");
  inspectorList(context, dependencySummary(data.dependencies), "No dependencies recorded.");
  inspectorDetail.append(context);
}

function showAudit(data) {
  inspectorAudit.replaceChildren();
  const audit = inspectorSection("Current conversation orientation audit");
  if (!activeThreadId) {
    inspectorList(audit, [], "Select a conversation in chat to inspect its orientation audit.");
  } else {
    inspectorValue(audit, "Conversation", data.thread_id);
    (data.turns || []).forEach((turn) => {
      const context = turn.knowledge_context || {};
      const item = document.createElement("article");
      item.className = "inspector-audit-turn";
      inspectorValue(item, `Turn ${turn.turn_number}`, context.status || "Not used");
      inspectorValue(item, "Snapshot", context.snapshot_id);
      inspectorValue(item, "Derived records", context.record_count);
      inspectorValue(item, "Budget", context.budget ? `${context.used_tokens ?? 0}/${context.budget.max_tokens ?? "?"} tokens` : "Not recorded");
      audit.append(item);
    });
    if (!data.turns?.length) inspectorList(audit, [], "No orientation audit has been recorded for this conversation.");
  }
  inspectorAudit.append(audit);
}

async function refreshInspectorAudit() {
  if (inspector.hidden) return;
  const threadId = activeThreadId;
  if (!threadId) {
    showAudit({ turns: [] });
    return;
  }
  showAudit({ thread_id: threadId, turns: [] });
  const audit = await inspectorJson(`/api/knowledge/threads/${threadId}/orientation`);
  if (activeThreadId === threadId && !inspector.hidden) showAudit(audit);
}

async function loadSnapshot(snapshotId) {
  inspectorStatus.textContent = "Loading snapshot…";
  showSnapshot(await inspectorJson(`/api/knowledge/snapshots/${snapshotId}`));
  inspectorStatus.textContent = "Read-only snapshot loaded.";
}

async function loadRecord(snapshotId, recordId) {
  inspectorStatus.textContent = "Loading derived record…";
  showRecord(await inspectorJson(`/api/knowledge/snapshots/${snapshotId}/records/${recordId}`));
  inspectorStatus.textContent = "Read-only derived record loaded.";
}

async function loadInspector() {
  inspectorStatus.textContent = "Loading read-only assimilation data…";
  const overview = await inspectorJson("/api/knowledge");
  showInspectorOverview(overview);
  if (overview.current_snapshot?.snapshot_id) await loadSnapshot(overview.current_snapshot.snapshot_id);
  else inspectorDetail.replaceChildren();
  await refreshInspectorAudit();
  inspectorStatus.textContent = "Read-only assimilation inspector.";
}

function showEmpty() {
  messages.replaceChildren();
  const empty = document.createElement("p");
  empty.className = "empty";
  empty.textContent = "Ask a question to start a private, source-grounded conversation.";
  messages.append(empty);
}

function showMessage(label, text) {
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
  message.append(heading, content);
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

function orientationSummary(context) {
  if (!context) return "Not used on this turn.";
  const status = context.status === "used" ? "Used" : "Orientation unavailable";
  const snapshot = typeof context.snapshot_id === "string" && context.snapshot_id ? context.snapshot_id : "snapshot unavailable";
  const records = Number.isInteger(context.record_count) ? `${context.record_count} record${context.record_count === 1 ? "" : "s"}` : "record count unavailable";
  const budget = context.budget || {};
  const budgetState = Number.isFinite(budget.used_tokens) && Number.isFinite(budget.max_tokens)
    ? `${budget.used_tokens}/${budget.max_tokens} budget`
    : "budget unavailable";
  return `${status} · ${snapshot} · ${records} · ${budgetState}`;
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
  ];
  rows.push(["Native compaction", diagnostics.native_compaction_applied
    ? "Applied for future model replay; included in this response usage."
    : "Not applied on this turn"]);
  if (diagnostics.knowledge_context) {
    rows.push(["Assimilated orientation", orientationSummary(diagnostics.knowledge_context)]);
  }
  const block = document.createElement("details");
  block.className = "diagnostics";
  const summary = document.createElement("summary");
  const orientationUnavailable = diagnostics.knowledge_context?.status === "unavailable";
  summary.textContent = orientationUnavailable ? "Evaluation diagnostics — orientation unavailable" : "Evaluation diagnostics";
  block.open = orientationUnavailable;
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
      refreshInspectorAudit().catch(() => showInspectorError("Could not load this conversation's orientation audit."));
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
  const response = await fetch(`/api/threads/${threadId}`);
  if (!response.ok) throw new Error("Could not restore this conversation.");
  const thread = await response.json();
  activeThreadId = thread.id;
  localStorage.setItem(ACTIVE_THREAD_KEY, String(thread.id));
  refreshInspectorAudit().catch(() => showInspectorError("Could not load this conversation's orientation audit."));
  renderTimeline(thread.turns);
  status.textContent = `Conversation: ${thread.title}`;
}

function renderTimeline(turns) {
  messages.replaceChildren();
  if (!turns.length) {
    showEmpty();
    return;
  }
  turns.forEach((turn) => {
    showMessage("Theo", turn.user_text);
    if (!turn.answer_markdown && !turn.incomplete_reason) return;
    const mentor = showMessage("Mentor", turn.answer_markdown || "");
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
    activeThreadId = undefined;
    localStorage.removeItem(ACTIVE_THREAD_KEY);
    await refreshInspectorAudit();
    showEmpty();
    status.textContent = "Conversation deleted.";
  }
  await loadThreads();
}

async function createThread(title = "New conversation") {
  const response = await fetch("/api/threads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
  if (!response.ok) throw new Error("Could not create a conversation.");
  const thread = await response.json();
  activeThreadId = thread.id;
  localStorage.setItem(ACTIVE_THREAD_KEY, String(thread.id));
  refreshInspectorAudit().catch(() => showInspectorError("Could not load this conversation's orientation audit."));
  showEmpty();
  await loadThreads();
}

function parseEventBuffer(buffer, onEvent) {
  const events = buffer.split("\n\n");
  events.slice(0, -1).forEach((event) => onEvent(JSON.parse(event.slice(5))));
  return events.at(-1);
}

async function sendMessage(text, showUser = true) {
  if (!activeThreadId) await createThread(conversationTitle(text));
  if (showUser) showMessage("Theo", text);
  status.textContent = "Thinking…";
  send.disabled = true;
  try {
    const response = await fetch(`/api/threads/${activeThreadId}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text, evaluation: evaluation() }) });
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
          if (event.type === "delta") {
            answerText += event.text;
            renderMarkdown(mentor.content, answerText);
          }
          if (event.type === "complete" || event.type === "incomplete") {
            terminal = true;
            renderMarkdown(mentor.content, event.answer.text);
            showEvidence(event.answer.evidence, event.answer.citations);
            showDiagnostics(event.answer.diagnostics);
            if (event.type === "incomplete") showIncomplete(event.answer, mentor);
          }
          if (event.type === "error") {
            terminal = true;
            showStreamError(mentor, event.error || "The mentor request failed. Try again.", text);
          }
        });
      }
      if (!terminal) {
        showStreamError(mentor, "The mentor stream ended before returning a usable response. Try again.", text);
        status.textContent = "Mentor unavailable. You can retry.";
        return;
      }
      if (mentor.heading.textContent === "Mentor — unavailable") {
        status.textContent = "Mentor unavailable. You can retry.";
        return;
      }
    } else {
      const answer = await response.json();
      const mentor = showMessage("Mentor", answer.text);
      showEvidence(answer.evidence, answer.citations);
      showDiagnostics(answer.diagnostics);
      if (answer.incomplete_reason) showIncomplete(answer, mentor);
    }
    await loadThreads();
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    send.disabled = false;
  }
}

document.querySelector("#new-thread").addEventListener("click", () => createThread().catch((error) => { status.textContent = error.message; }));
inspectorToggle.addEventListener("click", () => setInspectorOpen(true));
inspectorClose.addEventListener("click", () => setInspectorOpen(false));
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = question.value.trim();
  if (!text) return;
  question.value = "";
  await sendMessage(text);
});
restoreEvaluation();
effort.addEventListener("change", persistEvaluation);
mode.addEventListener("change", persistEvaluation);
researchDepth.addEventListener("change", persistEvaluation);
loadThreads().then(async () => {
  const savedThreadId = Number(localStorage.getItem(ACTIVE_THREAD_KEY));
  const firstThread = threads.querySelector(".thread");
  if (Number.isInteger(savedThreadId) && savedThreadId > 0) {
    try { await loadThread(savedThreadId); }
    catch { localStorage.removeItem(ACTIVE_THREAD_KEY); if (firstThread) firstThread.click(); else showEmpty(); }
  }
  else if (firstThread) firstThread.click();
  else showEmpty();
}).catch(() => { status.textContent = "Could not load conversations."; });

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
const profileToggle = document.querySelector("#profile-toggle");
const profilePanel = document.querySelector("#profile-panel");
const profileBackdrop = document.querySelector("#profile-backdrop");
const profileClose = document.querySelector("#profile-close");
const profileStatus = document.querySelector("#profile-status");
const profileAddForm = document.querySelector("#profile-add-form");
const profileCurrent = document.querySelector("#profile-current");
const profileTentative = document.querySelector("#profile-tentative");
const profileHistory = document.querySelector("#profile-history");
const profileConflicts = document.querySelector("#profile-conflicts");
const SETTINGS_KEY = "trading-mentor-evaluation-settings";
const ACTIVE_THREAD_KEY = "trading-mentor-active-thread";
const CONTINUE_PROMPT = "Continue the previous response from where it stopped. Do not repeat completed material.";
let activeThreadId;
let profileOpener = null;

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
    review.addEventListener("click", openProfile);
    block.append(review);
  } else {
    block.textContent = update.kind === "saved" ? "Saved to Trader Profile." : "Removed from Trader Profile.";
  }
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
    activeThreadId = undefined;
    localStorage.removeItem(ACTIVE_THREAD_KEY);
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
  showEmpty();
  await loadThreads();
}

const PROFILE_CATEGORY_LABELS = {
  "goals/research": "Goals and research",
  "markets/instruments": "Markets and instruments",
  "schedule/horizon": "Schedule and horizon",
  "style/methodology": "Style and methodology",
  "execution/risk/constraints": "Execution and risk",
  "experience/learning": "Experience and learning",
  "preferences/discretion": "Preferences and discretion",
  "strengths/difficulties/principles": "Strengths, difficulties and principles",
};
const PROFILE_PROVENANCE_LABELS = {
  USER_STATED: "You stated this",
  USER_CONFIRMED: "You confirmed this",
  AI_INFERRED: "Mentor proposal",
  USER_DECISION: "Your decision",
};
const PROFILE_STATE_LABELS = {
  confirmed: "Current",
  tentative: "Needs confirmation",
  superseded: "Superseded",
  conflicting: "Conflicting",
  archived: "Archived",
};

function profileMessage(message, error = false) {
  profileStatus.textContent = message;
  profileStatus.dataset.error = String(error);
}

async function profileRequest(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Could not update the Trader Profile.");
  return data;
}

function profileButton(label, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", () => action().catch((error) => profileMessage(error.message, true)));
  return button;
}

function profileEmpty(message) {
  const empty = document.createElement("p");
  empty.className = "profile-empty";
  empty.textContent = message;
  return empty;
}

function profileOrigin(item) {
  if (item.origin_kind === "profile-editor") return "Added in Trader Profile";
  if (item.origin_kind === "confirmation") return "Confirmed in Trader Profile";
  if (!item.origin_available) return "Original conversation is unavailable";
  return `Chat · conversation ${item.origin_thread_id}, turn ${item.origin_turn_number}`;
}

function renderProfileRecord(item, actions = []) {
  const record = document.createElement("article");
  record.className = "profile-record";
  const subject = document.createElement("strong");
  subject.textContent = item.subject;
  const value = document.createElement("p");
  value.textContent = item.value;
  const meta = document.createElement("small");
  meta.textContent = `${PROFILE_STATE_LABELS[item.state] || item.state} · ${PROFILE_PROVENANCE_LABELS[item.provenance] || item.provenance} · ${profileOrigin(item)}`;
  record.append(subject, value, meta);
  if (actions.length) {
    const controls = document.createElement("div");
    controls.className = "profile-record-actions";
    actions.forEach((action) => controls.append(action));
    record.append(controls);
  }
  return record;
}

function destructiveDelete(item) {
  return async () => {
    if (!window.confirm(`Permanently delete “${item.subject}”? This removes this local profile item.`)) return;
    await profileRequest(`/api/profile/items/${item.id}`, { method: "DELETE" });
    await loadProfile();
    profileMessage("Profile item permanently deleted.");
  };
}

function editProfileItem(item) {
  return async () => {
    const value = window.prompt(`Update ${item.subject}`, item.value);
    if (value === null) return;
    await profileRequest(`/api/profile/items/${item.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "edit", value, provenance: "USER_DECISION" }),
    });
    await loadProfile();
    profileMessage("Profile item updated.");
  };
}

function renderProfileGroups(target, items, emptyMessage, actionsFor) {
  target.replaceChildren();
  if (!items.length) {
    target.append(profileEmpty(emptyMessage));
    return;
  }
  const groups = new Map();
  items.forEach((item) => {
    const group = groups.get(item.category) || [];
    group.push(item);
    groups.set(item.category, group);
  });
  [...groups.entries()].sort(([left], [right]) => left.localeCompare(right)).forEach(([category, records]) => {
    const heading = document.createElement("h4");
    heading.textContent = PROFILE_CATEGORY_LABELS[category] || category;
    target.append(heading);
    records.forEach((item) => target.append(renderProfileRecord(item, actionsFor(item))));
  });
}

function renderProfile(data) {
  renderProfileGroups(profileCurrent, data.current, "No confirmed profile items yet.", (item) => [
    profileButton("Edit", editProfileItem(item)),
    profileButton("Archive", async () => {
      if (!window.confirm(`Archive “${item.subject}”? It will stop affecting future advice.`)) return;
      await profileRequest(`/api/profile/items/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "archive" }) });
      await loadProfile();
      profileMessage("Profile item archived.");
    }),
    profileButton("Delete", destructiveDelete(item)),
  ]);
  renderProfileGroups(profileTentative, data.tentative, "No profile updates need confirmation.", (item) => [
    profileButton("Confirm", async () => {
      await profileRequest(`/api/profile/items/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "confirm" }) });
      await loadProfile();
      profileMessage("Profile item confirmed.");
    }),
    profileButton("Reject", async () => {
      if (!window.confirm(`Reject “${item.subject}”? It will be archived.`)) return;
      await profileRequest(`/api/profile/items/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "reject" }) });
      await loadProfile();
      profileMessage("Profile proposal rejected.");
    }),
    profileButton("Delete", destructiveDelete(item)),
  ]);
  renderProfileGroups(profileHistory, data.history, "No profile history.", (item) => [profileButton("Delete", destructiveDelete(item))]);
  renderProfileGroups(profileConflicts, data.conflicts, "No unresolved profile conflicts.", (item) => [profileButton("Delete", destructiveDelete(item))]);
}

async function loadProfile() {
  profileMessage("Loading profile…");
  const data = await profileRequest("/api/profile");
  renderProfile(data);
  profileMessage("");
}

async function openProfile() {
  profileOpener = document.activeElement instanceof HTMLElement ? document.activeElement : profileToggle;
  profilePanel.hidden = false;
  profileBackdrop.hidden = false;
  profileToggle.setAttribute("aria-expanded", "true");
  profileClose.focus();
  try { await loadProfile(); }
  catch (error) { profileMessage(error.message, true); }
}

function closeProfile() {
  profilePanel.hidden = true;
  profileBackdrop.hidden = true;
  profileToggle.setAttribute("aria-expanded", "false");
  (profileOpener?.isConnected ? profileOpener : profileToggle).focus();
}

function profileFocusable() {
  return [...profilePanel.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex=\"-1\"])")];
}

function trapProfileFocus(event) {
  if (profilePanel.hidden || event.key !== "Tab") return;
  const focusable = profileFocusable();
  if (!focusable.length) {
    event.preventDefault();
    profilePanel.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey ? document.activeElement === first || !profilePanel.contains(document.activeElement) : document.activeElement === last || !profilePanel.contains(document.activeElement)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  }
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
            showProfileUpdate(event.answer.profile_update);
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
      showProfileUpdate(answer.profile_update);
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
profileToggle.addEventListener("click", openProfile);
profileClose.addEventListener("click", closeProfile);
profileBackdrop.addEventListener("click", closeProfile);
document.addEventListener("keydown", (event) => {
  if (!profilePanel.hidden && event.key === "Escape") closeProfile();
  else trapProfileFocus(event);
});
profileAddForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(profileAddForm));
  try {
    await profileRequest("/api/profile/items", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
    profileAddForm.reset();
    await loadProfile();
    profileMessage("Profile item added.");
  } catch (error) { profileMessage(error.message, true); }
});
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

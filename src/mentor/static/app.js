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
const SETTINGS_KEY = "trading-mentor-evaluation-settings";
const ACTIVE_THREAD_KEY = "trading-mentor-active-thread";
const CONTINUE_PROMPT = "Continue the previous response from where it stopped. Do not repeat completed material.";
let activeThreadId;

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
  const items = [...evidence].sort((left, right) => Number(cited.has(right.file_id)) - Number(cited.has(left.file_id)));
  if (!items.length && citations[0]) items.push({ ...citations[0], excerpt: "", metadata: {} });
  if (!items.length) return;
  const block = document.createElement("details");
  block.className = "evidence";
  const summary = document.createElement("summary");
  summary.textContent = `${cited.size} cited · ${evidence.length} retrieved evidence ${evidence.length === 1 ? "result" : "results"}`;
  const content = document.createElement("div");
  content.className = "evidence-content";
  const citedItems = [];
  const additionalItems = [];
  const seen = new Set();
  items.forEach((item) => {
    const key = `${item.file_id}:${item.excerpt}`;
    if (seen.has(key)) return;
    seen.add(key);
    (cited.has(item.file_id) ? citedItems : additionalItems).push(item);
  });
  citedItems.forEach((item) => content.append(evidenceItem(item)));
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
    content.append(toggle, additional);
  }
  block.append(summary, content);
  messages.append(block);
}

function evidenceItem(item) {
  const entry = document.createElement("div");
  entry.className = "evidence-item";
  const heading = document.createElement("strong");
  heading.textContent = formatEvidenceDate(item) || item.filename || "Source";
  const timestamp = formatEvidenceTimestamp(item.excerpt || "");
  const excerpt = document.createElement("span");
  excerpt.className = "evidence-excerpt";
  excerpt.textContent = shortOriginalExcerpt(item.excerpt || "");
  const link = document.createElement("a");
  link.href = `/api/sources/${encodeURIComponent(item.file_id)}`;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "Open full transcript";
  entry.append(heading);
  if (timestamp) {
    const time = document.createElement("small");
    time.textContent = timestamp;
    entry.append(time);
  }
  entry.append(excerpt, link);
  return entry;
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
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer = parseEventBuffer(buffer + decoder.decode(chunk.value, { stream: true }), (event) => {
          if (event.type === "delta") {
            answerText += event.text;
            renderMarkdown(mentor.content, answerText);
          }
          if (event.type === "complete" || event.type === "incomplete") {
            showEvidence(event.answer.evidence, event.answer.citations);
            showDiagnostics(event.answer.diagnostics);
            if (event.type === "incomplete") showIncomplete(event.answer, mentor);
          }
        });
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

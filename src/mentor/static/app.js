import { marked } from "/vendor/marked.esm.js";

const threads = document.querySelector("#threads");
const messages = document.querySelector("#messages");
const form = document.querySelector("#question-form");
const question = document.querySelector("#question");
const status = document.querySelector("#status");
const send = form.querySelector("button[type=submit]");
const effort = document.querySelector("#reasoning-effort");
const mode = document.querySelector("#reasoning-mode");
const SETTINGS_KEY = "trading-mentor-evaluation-settings";
const CONTINUE_PROMPT = "Continue the previous response from where it stopped. Do not repeat completed material.";
let activeThreadId;

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
  target.querySelectorAll("a").forEach((link) => {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  });
}

function showEvidence(evidence, citations) {
  const cited = new Set(citations.map((citation) => citation.file_id));
  const relevant = evidence.filter((item) => cited.has(item.file_id));
  const items = relevant.length ? relevant : evidence.slice(0, 1);
  if (!items.length && citations[0]) items.push({ ...citations[0], excerpt: "", metadata: {} });
  if (!items.length) return;
  const block = document.createElement("details");
  block.className = "evidence";
  const summary = document.createElement("summary");
  summary.textContent = `${items.length} ${relevant.length ? "cited" : "retrieved"} evidence ${items.length === 1 ? "result" : "results"}`;
  const content = document.createElement("div");
  content.className = "evidence-content";
  const seen = new Set();
  items.forEach((item) => {
    const key = `${item.file_id}:${item.excerpt}`;
    if (seen.has(key)) return;
    seen.add(key);
    const entry = document.createElement("div");
    entry.className = "evidence-item";
    const link = document.createElement("a");
    link.href = `/api/sources/${encodeURIComponent(item.file_id)}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = `Open full transcript: ${item.filename}${item.year ? ` (${item.year})` : ""}`;
    const excerpt = document.createElement("span");
    excerpt.className = "evidence-excerpt";
    excerpt.textContent = item.excerpt || "No retrieved excerpt was returned for this citation.";
    const metadata = document.createElement("small");
    metadata.textContent = item.metadata?.relative_path || "";
    entry.append(link, excerpt, metadata);
    content.append(entry);
  });
  block.append(summary, content);
  messages.append(block);
}

function showDiagnostics(diagnostics) {
  if (!diagnostics) return;
  const rows = [
    ["Model", diagnostics.model],
    ["Reasoning", `${diagnostics.reasoning_effort} / ${diagnostics.reasoning_mode}`],
    ["Status", diagnostics.status],
    ["Latency", `${(diagnostics.latency_ms / 1000).toFixed(1)}s`],
    ["Tokens", `${diagnostics.input_tokens ?? "—"} input · ${diagnostics.output_tokens ?? "—"} output · ${diagnostics.reasoning_tokens ?? "—"} reasoning`],
    ["Text-token estimate", diagnostics.estimated_text_cost_usd == null ? "Unavailable" : `$${diagnostics.estimated_text_cost_usd.toFixed(4)} (excludes File Search fees)`],
  ];
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
  return { reasoning_effort: effort.value, reasoning_mode: mode.value };
}

function restoreEvaluation() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY));
    if (["high", "xhigh", "max"].includes(saved?.reasoning_effort)) effort.value = saved.reasoning_effort;
    if (["standard", "pro"].includes(saved?.reasoning_mode)) mode.value = saved.reasoning_mode;
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
    const button = document.createElement("button");
    button.className = "thread";
    button.type = "button";
    button.textContent = thread.title;
    button.setAttribute("aria-current", String(thread.id === activeThreadId));
    button.addEventListener("click", () => {
      activeThreadId = thread.id;
      messages.replaceChildren();
      status.textContent = `Conversation: ${thread.title}`;
      loadThreads().catch(() => { status.textContent = "Could not refresh conversations."; });
    });
    threads.append(button);
  });
}

async function createThread(title = "New conversation") {
  const response = await fetch("/api/threads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
  if (!response.ok) throw new Error("Could not create a conversation.");
  const thread = await response.json();
  activeThreadId = thread.id;
  messages.replaceChildren();
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
loadThreads().catch(() => { status.textContent = "Could not load conversations."; });

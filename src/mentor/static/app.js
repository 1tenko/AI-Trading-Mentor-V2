const threads = document.querySelector("#threads");
const messages = document.querySelector("#messages");
const form = document.querySelector("#question-form");
const question = document.querySelector("#question");
const status = document.querySelector("#status");
let activeThreadId;

function showMessage(label, text) {
  const message = document.createElement("section");
  message.className = `message message--${label.toLowerCase()}`;
  const heading = document.createElement("strong");
  heading.className = "message-label";
  heading.textContent = label;
  const content = document.createElement("div");
  content.className = label === "Mentor" ? "message-content markdown" : "message-content";
  if (label === "Mentor") {
    renderMarkdown(content, text);
  } else {
    content.textContent = text;
  }
  message.append(heading, content);
  messages.append(message);
  return { message, content };
}

function renderMarkdown(target, text) {
  const html = marked.parse(text, { breaks: true, gfm: true });
  target.innerHTML = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
  target.querySelectorAll("a").forEach((link) => {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  });
}

function showEvidence(evidence, citations) {
  const cited = new Set(citations.map((citation) => citation.file_id));
  const item = evidence.find((entry) => cited.has(entry.file_id)) || evidence[0];
  if (item) {
    const block = document.createElement("section");
    block.className = "evidence";
    const link = document.createElement("a");
    link.href = `/api/sources/${encodeURIComponent(item.file_id)}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = `${cited.has(item.file_id) ? "Cited evidence: " : "Retrieved evidence: "}${item.filename}${item.year ? ` (${item.year})` : ""}`;
    const excerpt = document.createElement("div");
    excerpt.textContent = item.excerpt;
    const metadata = document.createElement("small");
    metadata.textContent = item.metadata.relative_path || "";
    block.append(link, excerpt, metadata);
    messages.append(block);
    return;
  }
  if (citations[0]) {
    const citation = citations[0];
    const link = document.createElement("a");
    link.href = `/api/sources/${encodeURIComponent(citation.file_id)}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = `Cited source: ${citation.filename}`;
    messages.append(link);
  }
}

async function loadThreads() {
  const response = await fetch("/api/threads");
  const data = await response.json();
  threads.replaceChildren();
  data.threads.forEach((thread) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = thread.title;
    button.addEventListener("click", () => { activeThreadId = thread.id; messages.replaceChildren(); status.textContent = `Conversation: ${thread.title}`; });
    threads.append(button);
  });
}

async function createThread() {
  const response = await fetch("/api/threads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "New conversation" }) });
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

document.querySelector("#new-thread").addEventListener("click", createThread);
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeThreadId) await createThread();
  const text = question.value.trim();
  if (!text) return;
  showMessage("Theo", text);
  question.value = "";
  status.textContent = "Thinking…";
  try {
    const response = await fetch(`/api/threads/${activeThreadId}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text }) });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || "The mentor is unavailable.");
    }
    if (response.headers.get("Content-Type").startsWith("text/event-stream")) {
      const answer = showMessage("Mentor", "");
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
            renderMarkdown(answer.content, answerText);
          }
          if (event.type === "complete") showEvidence(event.answer.evidence, event.answer.citations);
        });
      }
    } else {
      const data = await response.json();
      showMessage("Mentor", data.text);
      showEvidence(data.evidence, data.citations);
    }
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
  }
});

loadThreads().catch(() => { status.textContent = "Could not load conversations."; });
import { marked } from "/vendor/marked.esm.js";

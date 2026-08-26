const form = document.querySelector("#questionnaire-form");
const status = document.querySelector("#profile-status");
const suggestions = document.querySelector("#profile-suggestions");
const history = document.querySelector("#profile-history");

async function profileRequest(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Could not update the Trader Profile.");
  return data;
}

function section(title) {
  const element = document.createElement("section");
  element.className = "questionnaire-section";
  const heading = document.createElement("h2");
  heading.textContent = title;
  element.append(heading);
  form.append(element);
  return element;
}

function field(parent, item, answer) {
  const wrapper = document.createElement("div");
  wrapper.className = "questionnaire-field";
  const label = document.createElement("label");
  label.htmlFor = `profile-${item.key}`;
  label.textContent = item.key === "additional_information" ? item.question : `${item.key.slice(1)}. ${item.question}`;
  const textarea = document.createElement("textarea");
  textarea.id = `profile-${item.key}`;
  textarea.name = item.key;
  textarea.rows = item.key === "additional_information" ? 7 : 4;
  textarea.maxLength = 500;
  textarea.value = answer?.value || "";
  const helper = document.createElement("p");
  helper.className = "subtitle";
  helper.textContent = item.helper;
  wrapper.append(label, textarea, helper);
  parent.append(wrapper);
}

function action(label, operation) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", () => operation().catch((error) => { status.textContent = error.message; }));
  return button;
}

function record(item) {
  const element = document.createElement("article");
  element.className = "profile-record";
  const subject = document.createElement("strong");
  subject.textContent = item.subject;
  const value = document.createElement("p");
  value.textContent = item.value;
  element.append(subject, value);
  return element;
}

async function loadSecondary() {
  const data = await profileRequest("/api/profile");
  suggestions.replaceChildren();
  if (data.tentative.length) {
    const heading = document.createElement("h2");
    heading.textContent = "Mentor suggestions to confirm";
    const explanation = document.createElement("p");
    explanation.className = "subtitle";
    explanation.textContent = "These are suggestions, not active profile information. Confirm or reject each one yourself.";
    suggestions.append(heading, explanation);
    data.tentative.forEach((item) => {
      const entry = record(item);
      const controls = document.createElement("div");
      controls.className = "profile-record-actions";
      controls.append(
        action("Confirm", async () => {
          await profileRequest(`/api/profile/items/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "confirm" }) });
          await loadSecondary();
        }),
        action("Reject", async () => {
          await profileRequest(`/api/profile/items/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "reject" }) });
          await loadSecondary();
        }),
      );
      entry.append(controls);
      suggestions.append(entry);
    });
    suggestions.hidden = false;
  } else {
    suggestions.hidden = true;
  }

  const retained = [...data.current, ...data.history, ...data.conflicts];
  const content = history.querySelector("div");
  content.replaceChildren();
  if (!retained.length) {
    content.textContent = "No other saved profile history yet.";
    return;
  }
  retained.forEach((item) => content.append(record(item)));
}

async function load() {
  const questionnaire = await profileRequest("/api/profile/questionnaire");
  let current;
  questionnaire.fields.forEach((item) => {
    if (!current || current.dataset.section !== item.section) {
      current = section(item.section);
      current.dataset.section = item.section;
    }
    field(current, item, questionnaire.answers[item.key]);
  });
  const save = document.createElement("button");
  save.type = "submit";
  save.className = "send";
  save.textContent = "Save Trader Profile";
  form.append(save);
  form.addEventListener("input", () => { status.textContent = "Unsaved changes"; });
  await loadSecondary();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const save = form.querySelector("button[type=submit]");
  const answers = Object.fromEntries(new FormData(form).entries());
  status.textContent = "Saving…";
  save.disabled = true;
  try {
    await profileRequest("/api/profile/questionnaire", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answers }) });
    status.textContent = "Saved";
    await loadSecondary();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    save.disabled = false;
  }
});

load().catch((error) => { status.textContent = error.message; });

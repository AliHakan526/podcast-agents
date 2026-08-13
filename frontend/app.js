const STORAGE_KEY = "podcastAgentsFrontendConfig";
const CONVERSATION_KEY = "podcastAgentsConversationId";
const POLL_INTERVAL_MS = 4000;

const elements = {
  settingsToggle: document.querySelector("#settingsToggle"),
  settingsPanel: document.querySelector("#settingsPanel"),
  saveSettings: document.querySelector("#saveSettings"),
  apiBaseUrl: document.querySelector("#apiBaseUrl"),
  jobsPath: document.querySelector("#jobsPath"),
  statusPath: document.querySelector("#statusPath"),
  chatForm: document.querySelector("#chatForm"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  stopPolling: document.querySelector("#stopPolling"),
  statusPill: document.querySelector("#statusPill"),
  chatMessages: document.querySelector("#chatMessages"),
};

let activeJobId = "";
let activeAssistantMessage = null;
let pollTimer = null;

function loadConfig() {
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  elements.apiBaseUrl.value = saved.apiBaseUrl || "";
  elements.jobsPath.value = saved.jobsPath || "/jobs";
  elements.statusPath.value = saved.statusPath || "/status";
}

function saveConfig() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(getConfig()));
  setStatus("READY");
}

function getConfig() {
  return {
    apiBaseUrl: elements.apiBaseUrl.value.trim().replace(/\/+$/, ""),
    jobsPath: normalizePath(elements.jobsPath.value || "/jobs"),
    statusPath: normalizePath(elements.statusPath.value || "/status"),
  };
}

function normalizePath(value) {
  const trimmed = value.trim();
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

function endpoint(path) {
  const config = getConfig();
  if (!config.apiBaseUrl) {
    throw new Error("Set your API base URL first.");
  }
  return `${config.apiBaseUrl}${path}`;
}

function getConversationId() {
  let conversationId = localStorage.getItem(CONVERSATION_KEY);
  if (!conversationId) {
    conversationId = crypto.randomUUID();
    localStorage.setItem(CONVERSATION_KEY, conversationId);
  }
  return conversationId;
}

async function sendMessage(event) {
  event.preventDefault();

  const message = elements.messageInput.value.trim();
  if (!message) return;

  localStorage.setItem(STORAGE_KEY, JSON.stringify(getConfig()));
  appendMessage("user", message);
  elements.messageInput.value = "";
  setBusy(true);
  setStatus("PROCESSING");
  activeAssistantMessage = appendMessage("assistant", "Thinking...");

  try {
    const response = await fetch(endpoint(getConfig().jobsPath), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        conversation_id: getConversationId(),
      }),
    });

    const data = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(data.error || `Message failed with HTTP ${response.status}`);
    }

    activeJobId = data.job_id;
    startPolling();
  } catch (error) {
    renderAssistantError(error.message);
    setStatus("FAILED");
    setBusy(false);
  }
}

function startPolling() {
  stopPolling(false);
  elements.stopPolling.disabled = false;
  pollStatus();
  pollTimer = window.setInterval(pollStatus, POLL_INTERVAL_MS);
}

function stopPolling(updateUi = true) {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
  elements.stopPolling.disabled = true;
  if (updateUi) {
    setStatus("READY");
  }
}

async function pollStatus() {
  if (!activeJobId) return;

  const url = new URL(endpoint(getConfig().statusPath));
  url.searchParams.set("job_id", activeJobId);

  try {
    const response = await fetch(url.toString());
    const data = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(data.error || `Status check failed with HTTP ${response.status}`);
    }

    if (data.status === "COMPLETED") {
      renderAssistantResponse(data.response || "", data.audio_url || "");
      stopPolling(false);
      setStatus("READY");
      setBusy(false);
      return;
    }

    if (data.status === "FAILED") {
      renderAssistantError(data.error || "The agent failed.");
      stopPolling(false);
      setStatus("FAILED");
      setBusy(false);
      return;
    }

    setStatus(data.status || "PROCESSING");
  } catch (error) {
    renderAssistantError(error.message);
    stopPolling(false);
    setStatus("FAILED");
    setBusy(false);
  }
}

function appendMessage(role, text) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = formatText(text);

  article.appendChild(bubble);
  elements.chatMessages.appendChild(article);
  elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
  return article;
}

function renderAssistantResponse(text, audioUrl) {
  const target = activeAssistantMessage || appendMessage("assistant", "");
  const bubble = target.querySelector(".bubble");
  bubble.innerHTML = formatText(text || "Done.");

  if (audioUrl) {
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = audioUrl;
    bubble.appendChild(audio);

    const link = document.createElement("a");
    link.href = audioUrl;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = "Open audio";
    bubble.appendChild(link);
  }

  elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
  activeAssistantMessage = null;
}

function renderAssistantError(message) {
  const target = activeAssistantMessage || appendMessage("assistant", "");
  const bubble = target.querySelector(".bubble");
  bubble.innerHTML = `<p class="error-text">${escapeHtml(message)}</p>`;
  activeAssistantMessage = null;
}

function formatText(text) {
  return escapeHtml(text)
    .split(/\n{2,}/)
    .map((block) => `<p>${block.replace(/\n/g, "<br />")}</p>`)
    .join("");
}

async function readJsonResponse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { error: text };
  }
}

function setBusy(isBusy) {
  elements.sendButton.disabled = isBusy;
  elements.messageInput.disabled = isBusy;
}

function setStatus(status) {
  const normalized = String(status || "READY").toUpperCase();
  elements.statusPill.textContent = normalized;
  elements.statusPill.className = "status-pill";
  if (normalized === "PROCESSING") elements.statusPill.classList.add("processing");
  else if (normalized === "FAILED") elements.statusPill.classList.add("failed");
  else elements.statusPill.classList.add("idle");
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

elements.settingsToggle.addEventListener("click", () => {
  const hidden = elements.settingsPanel.classList.toggle("is-hidden");
  elements.settingsToggle.setAttribute("aria-expanded", String(!hidden));
});

elements.saveSettings.addEventListener("click", saveConfig);
elements.chatForm.addEventListener("submit", sendMessage);
elements.stopPolling.addEventListener("click", () => stopPolling(true));

loadConfig();
setStatus("READY");

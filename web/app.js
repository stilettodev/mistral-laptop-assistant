// Mistral Laptop Assistant — frontend
// SSE-driven chat: streams events from /api/chat and renders them.

const thread   = document.getElementById("thread");
const composer = document.getElementById("composer");
const input    = document.getElementById("input");
const sendBtn  = document.getElementById("sendBtn");
const modelPicker = document.getElementById("modelPicker");
const modelHint   = document.getElementById("modelHint");
const safetySeg   = document.getElementById("safetySeg");
const newChatBtn  = document.getElementById("newChat");
const statusEl    = document.getElementById("status");

const state = {
  conversationId: crypto.randomUUID(),
  safety: "normal",
  pendingConfirmations: null, // {calls: [...]} when waiting
  busy: false,
  modelsById: {},
};

// ── Boot ─────────────────────────────────────────────────────────────

(async () => {
  await Promise.all([loadStatus(), loadModels()]);
  bindUI();
})();

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    statusEl.querySelector(".dot").className =
      "dot " + (data.api_key_configured ? "ok" : "err");
    statusEl.querySelector(".status-text").textContent = data.api_key_configured
      ? `online · ${shorten(data.platform)}`
      : "no API key — set MLA_MISTRAL_API_KEY";
  } catch (e) {
    statusEl.querySelector(".dot").className = "dot err";
    statusEl.querySelector(".status-text").textContent = "server unreachable";
  }
}

async function loadModels() {
  const res = await fetch("/api/models");
  const data = await res.json();
  modelPicker.innerHTML = "";
  for (const m of data.models) {
    state.modelsById[m.id] = m;
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.id === "auto" ? "🤖 auto — let the agent pick" : m.id;
    modelPicker.appendChild(opt);
  }
  modelPicker.value = "auto";
  updateModelHint();
}

function bindUI() {
  modelPicker.addEventListener("change", updateModelHint);

  safetySeg.querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => {
      safetySeg.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.safety = b.dataset.mode;
    });
  });

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    submit();
  });

  input.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  });

  newChatBtn.addEventListener("click", async () => {
    await fetch(`/api/conversations/${state.conversationId}/reset`, { method: "POST" });
    state.conversationId = crypto.randomUUID();
    state.pendingConfirmations = null;
    thread.innerHTML = "";
    input.focus();
  });

  document.querySelectorAll("#examples li").forEach((li) => {
    li.addEventListener("click", () => {
      input.value = li.textContent;
      input.focus();
    });
  });
}

function updateModelHint() {
  const m = state.modelsById[modelPicker.value];
  modelHint.textContent = m ? m.description : "";
}

function shorten(p) {
  if (!p) return "";
  return p.length > 36 ? p.slice(0, 33) + "…" : p;
}

// ── Submit / SSE ─────────────────────────────────────────────────────

async function submit() {
  if (state.busy) return;
  const text = input.value.trim();
  if (!text && !state.pendingConfirmations) return;

  if (text) {
    addUserMessage(text);
    input.value = "";
  }

  await runRequest({
    message: text || "(confirmation reply)",
    model: modelPicker.value,
    safety_mode: state.safety,
    confirmations: {},
    reset: false,
  });
}

async function runRequest(payload, extraConfirmations = {}) {
  state.busy = true;
  sendBtn.disabled = true;

  // Merge confirmations from any pending block
  payload.confirmations = { ...payload.confirmations, ...extraConfirmations };

  // Show placeholder
  const placeholder = addAssistantMessage("");
  const bodyEl = placeholder.querySelector(".body");
  bodyEl.innerHTML = `<span class="typing">thinking…</span>`;

  const ctrl = new AbortController();
  let res;
  try {
    res = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-conversation-id": state.conversationId,
      },
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
  } catch (e) {
    showError(`Network error: ${e.message}`);
    cleanup();
    return;
  }

  if (!res.ok) {
    let err = await res.text();
    try { err = JSON.parse(err).detail || err; } catch (_) {}
    showError(err);
    placeholder.remove();
    cleanup();
    return;
  }

  // Stream SSE
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let placeholderUsed = false;
  let currentAssistant = placeholder;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop();
    for (const block of events) {
      const evt = parseEvent(block);
      if (!evt) continue;

      switch (evt.event) {
        case "conversation":
          state.conversationId = evt.data.id;
          break;

        case "model": {
          // Show which model + why
          const badge = document.createElement("div");
          badge.className = "model-badge";
          badge.innerHTML = `<span>via <span class="via">${evt.data.via}</span></span> · <strong>${evt.data.model}</strong> · ${escapeHtml(evt.data.reason)}`;
          thread.appendChild(badge);
          scrollDown();
          break;
        }

        case "status":
          if (currentAssistant) {
            currentAssistant.querySelector(".body").innerHTML =
              `<span class="typing">${escapeHtml(evt.data)}</span>`;
          }
          break;

        case "message":
          if (currentAssistant) {
            currentAssistant.querySelector(".body").innerHTML = renderMarkdown(evt.data);
            placeholderUsed = true;
            scrollDown();
          }
          break;

        case "tool_call": {
          if (!placeholderUsed && currentAssistant) {
            currentAssistant.remove();
            currentAssistant = null;
          }
          renderToolCall(evt.data);
          break;
        }

        case "tool_result":
          updateToolResult(evt.data);
          // After a tool result, prepare new assistant placeholder
          if (!currentAssistant) {
            currentAssistant = addAssistantMessage("");
            currentAssistant.querySelector(".body").innerHTML =
              `<span class="typing">processing result…</span>`;
            placeholderUsed = false;
          }
          break;

        case "confirmation_needed":
          if (currentAssistant && !placeholderUsed) {
            currentAssistant.remove();
            currentAssistant = null;
          }
          renderConfirmation(evt.data);
          state.pendingConfirmations = { calls: evt.data };
          break;

        case "final":
          if (currentAssistant) {
            currentAssistant.querySelector(".body").innerHTML = renderMarkdown(evt.data);
            placeholderUsed = true;
          }
          break;

        case "error":
          showError(typeof evt.data === "string" ? evt.data : JSON.stringify(evt.data));
          if (currentAssistant && !placeholderUsed) currentAssistant.remove();
          break;

        case "done":
          break;
      }
      scrollDown();
    }
  }

  // If we never rendered final text into placeholder, remove it
  if (currentAssistant && !placeholderUsed && currentAssistant.querySelector(".typing")) {
    currentAssistant.remove();
  }

  cleanup();
}

function cleanup() {
  state.busy = false;
  sendBtn.disabled = false;
  input.focus();
}

function parseEvent(block) {
  const lines = block.split("\n");
  let evt = "message";
  const dataLines = [];
  for (const l of lines) {
    if (l.startsWith("event:")) evt = l.slice(6).trim();
    else if (l.startsWith("data:")) dataLines.push(l.slice(5).trim());
  }
  if (!dataLines.length) return null;
  const raw = dataLines.join("\n");
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    data = raw;
  }
  return { event: evt, data };
}

// ── Rendering helpers ────────────────────────────────────────────────

function addUserMessage(text) {
  const el = renderMessage("user", "you", text);
  thread.appendChild(el);
  scrollDown();
  return el;
}

function addAssistantMessage(text) {
  const el = renderMessage("assistant", "mla", text);
  thread.appendChild(el);
  scrollDown();
  return el;
}

function renderMessage(role, who, text) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = `
    <div class="avatar">${role === "user" ? "U" : "M"}</div>
    <div class="bubble">
      <div class="who">${who}</div>
      <div class="body">${renderMarkdown(text)}</div>
    </div>`;
  return el;
}

function renderToolCall(data) {
  const card = document.createElement("div");
  card.className = "tool-card";
  card.dataset.callId = data.id;
  card.innerHTML = `
    <div class="tool-head">
      <div class="tool-name"><span class="fn">${escapeHtml(data.name)}</span>(<span class="args-inline">${escapeHtml(inlineArgs(data.arguments))}</span>)</div>
      <div class="tool-status">running…</div>
    </div>
    <div class="tool-body">
      <pre class="args">${escapeHtml(JSON.stringify(data.arguments, null, 2))}</pre>
    </div>`;
  card.querySelector(".tool-head").addEventListener("click", () => {
    card.classList.toggle("open");
  });
  thread.appendChild(card);
  scrollDown();
}

function updateToolResult(data) {
  const card = thread.querySelector(`.tool-card[data-call-id="${data.id}"]`);
  if (!card) return;
  const ok = data.result && data.result.ok;
  card.classList.add(data.denied ? "denied" : ok ? "ok" : "denied");
  card.querySelector(".tool-status").textContent = data.denied
    ? "denied"
    : ok
    ? `ok · ${data.result.duration_ms ?? 0}ms`
    : "error";

  const body = card.querySelector(".tool-body");

  // Add result block
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(stripPreview(data.result), null, 2);
  body.appendChild(pre);

  // Render screenshot preview if any
  if (data.result && data.result.preview_base64) {
    const img = document.createElement("img");
    img.className = "preview-img";
    img.src = "data:image/png;base64," + data.result.preview_base64;
    body.appendChild(img);
  }
}

function stripPreview(obj) {
  if (!obj || typeof obj !== "object") return obj;
  const copy = { ...obj };
  if (copy.preview_base64) copy.preview_base64 = `<${copy.preview_base64.length} bytes>`;
  return copy;
}

function renderConfirmation(calls) {
  const card = document.createElement("div");
  card.className = "confirm-card";
  card.innerHTML = `
    <h4>Approve before running?</h4>
    <p class="desc">The assistant wants to perform an action that could change your system. Review the details below.</p>
    <div class="confirm-list">
      ${calls.map((c) => `
        <div class="confirm-item">
          <div><span class="fn">${escapeHtml(c.name)}</span>(${escapeHtml(inlineArgs(c.arguments))})</div>
          <pre>${escapeHtml(JSON.stringify(c.arguments, null, 2))}</pre>
        </div>
      `).join("")}
    </div>
    <div class="confirm-buttons">
      <button class="btn approve">Approve all</button>
      <button class="btn deny">Deny</button>
    </div>
  `;
  thread.appendChild(card);

  card.querySelector(".approve").addEventListener("click", () => {
    const cs = {};
    for (const c of calls) cs[c.id] = true;
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    card.remove();
    state.pendingConfirmations = null;
    runRequest({
      message: "(approved)",
      model: modelPicker.value,
      safety_mode: state.safety,
      confirmations: cs,
      reset: false,
    });
  });

  card.querySelector(".deny").addEventListener("click", () => {
    const cs = {};
    for (const c of calls) cs[c.id] = false;
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    card.remove();
    state.pendingConfirmations = null;
    runRequest({
      message: "(denied)",
      model: modelPicker.value,
      safety_mode: state.safety,
      confirmations: cs,
      reset: false,
    });
  });

  scrollDown();
}

function showError(msg) {
  const el = document.createElement("div");
  el.className = "error-card";
  el.textContent = "✕  " + msg;
  thread.appendChild(el);
  scrollDown();
}

function inlineArgs(args) {
  if (!args) return "";
  const parts = [];
  for (const [k, v] of Object.entries(args)) {
    let val = typeof v === "string" ? `"${v}"` : JSON.stringify(v);
    if (val.length > 60) val = val.slice(0, 57) + "…";
    parts.push(`${k}=${val}`);
  }
  return parts.join(", ");
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderMarkdown(text) {
  if (!text) return "";
  // Tiny markdown: code fences, inline code, **bold**, *italic*, links.
  let html = escapeHtml(text);
  html = html.replace(/```([a-z]*)\n([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code class="lang-${lang}">${code}</code></pre>`
  );
  html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|\s)\*([^*\n]+)\*/g, "$1<em>$2</em>");
  html = html.replace(/\[([^\]]+)\]\((https?:[^\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return html;
}

function scrollDown() {
  requestAnimationFrame(() => thread.scrollTo({ top: thread.scrollHeight }));
}

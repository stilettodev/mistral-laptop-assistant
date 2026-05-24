// Mistral Laptop Assistant — frontend
// Handles SSE chat, voice in/out, image attachments, tabs (history /
// jobs / memory / settings) and conversation persistence.

const $ = (id) => document.getElementById(id);

const thread       = $("thread");
const composer     = $("composer");
const input        = $("input");
const sendBtn      = $("sendBtn");
const modelPicker  = $("modelPicker");
const modelHint    = $("modelHint");
const safetySeg    = $("safetySeg");
const newChatBtn   = $("newChat");
const statusEl     = $("status");
const tabs         = $("tabs");
const micBtn       = $("micBtn");
const attachBtn    = $("attachBtn");
const fileInput    = $("fileInput");
const attachStrip  = $("attachStrip");
const speakToggle  = $("speakToggle");

const state = {
  conversationId: crypto.randomUUID(),
  safety: "normal",
  pendingConfirmations: null,
  busy: false,
  modelsById: {},
  capabilities: { voice: { stt: false, tts: false } },
  attachments: [],          // {name, data_url}
  recorder: null,
  recording: false,
  audioPlayer: null,
};

// ── Boot ─────────────────────────────────────────────────────────────

(async () => {
  await Promise.all([loadStatus(), loadModels(), loadCapabilities()]);
  bindUI();
})();

async function loadStatus() {
  try {
    const data = await (await fetch("/api/status")).json();
    statusEl.querySelector(".dot").className =
      "dot " + (data.api_key_configured ? "ok" : "err");
    statusEl.querySelector(".status-text").textContent = data.api_key_configured
      ? `online · ${shorten(data.platform)}`
      : "no API key — set MLA_MISTRAL_API_KEY";
    fillSettings(data);
  } catch {
    statusEl.querySelector(".dot").className = "dot err";
    statusEl.querySelector(".status-text").textContent = "server unreachable";
  }
}

async function loadCapabilities() {
  try {
    state.capabilities = await (await fetch("/api/capabilities")).json();
  } catch { /* keep defaults */ }
  micBtn.style.display = state.capabilities.voice.stt ? "" : "none";
  // Always show speak toggle; backend will tell us if it can't.
}

async function loadModels() {
  const { models } = await (await fetch("/api/models")).json();
  modelPicker.innerHTML = "";
  for (const m of models) {
    state.modelsById[m.id] = m;
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.id === "auto" ? "🤖 auto — let the agent pick" : m.id;
    modelPicker.appendChild(opt);
  }
  modelPicker.value = "auto";
  updateModelHint();
}

function fillSettings(status) {
  const grid = $("settingsGrid");
  grid.innerHTML = "";
  const rows = [
    ["API key",      status.api_key_configured ? "✅ loaded" : "❌ missing"],
    ["Platform",     status.platform],
    ["Workspace",    status.workspace_dir],
    ["Audit log",    status.audit_log],
    ["Safety",       status.safety_mode],
    ["Default model",status.default_model],
  ];
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    grid.append(dt, dd);
  }
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
    state.attachments = [];
    renderAttachments();
    thread.innerHTML = "";
    input.focus();
    refreshHistory();
  });

  document.querySelectorAll("#examples li").forEach((li) => {
    li.addEventListener("click", () => {
      input.value = li.textContent;
      input.focus();
    });
  });

  // Tabs
  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tab]");
    if (!btn) return;
    tabs.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
    btn.classList.add("active");
    const which = btn.dataset.tab;
    document.querySelectorAll(".tab-pane").forEach((p) => {
      p.classList.toggle("hidden", p.dataset.pane !== which);
    });
    if (which === "history") refreshHistory();
    if (which === "jobs")    refreshJobs();
    if (which === "memory")  refreshMemory();
  });

  // Voice in
  micBtn.addEventListener("click", toggleRecording);

  // Attachments – click + drag-drop
  attachBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    for (const f of e.target.files) uploadAttachment(f);
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) =>
    composer.addEventListener(evt, (e) => {
      e.preventDefault();
      composer.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    composer.addEventListener(evt, (e) => {
      e.preventDefault();
      composer.classList.remove("dragover");
    })
  );
  composer.addEventListener("drop", (e) => {
    for (const f of e.dataTransfer.files) {
      if (f.type.startsWith("image/")) uploadAttachment(f);
    }
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

// ── Sidebar lists ─────────────────────────────────────────────────────

async function refreshHistory() {
  const list = $("historyList");
  list.innerHTML = '<li class="row-meta">loading…</li>';
  try {
    const { conversations } = await (await fetch("/api/conversations")).json();
    if (!conversations.length) {
      list.innerHTML = '<li class="row-meta">no saved chats yet</li>';
      return;
    }
    list.innerHTML = "";
    for (const c of conversations) {
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="row-title">${escapeHtml(c.title)}</span>
        <span class="row-meta">
          <span>${c.messages} msgs · ${timeAgo(c.updated_at)}</span>
          <button data-id="${c.id}" title="Delete">✕</button>
        </span>`;
      li.addEventListener("click", (e) => {
        if (e.target.tagName === "BUTTON") return;
        loadConversation(c.id);
      });
      li.querySelector("button").addEventListener("click", async (e) => {
        e.stopPropagation();
        await fetch(`/api/conversations/${c.id}/reset`, { method: "POST" });
        refreshHistory();
      });
      list.appendChild(li);
    }
  } catch (e) {
    list.innerHTML = `<li class="row-meta">${escapeHtml(e.message)}</li>`;
  }
}

async function loadConversation(cid) {
  const { messages } = await (await fetch(`/api/conversations/${cid}`)).json();
  state.conversationId = cid;
  state.pendingConfirmations = null;
  thread.innerHTML = "";
  for (const m of messages) {
    if (m.role === "user") {
      addUserMessage(m.content || "");
    } else if (m.role === "assistant" && m.content) {
      addAssistantMessage(m.content);
    } else if (m.role === "tool") {
      // show a compact past tool record
      const card = document.createElement("div");
      card.className = "tool-card ok open";
      card.innerHTML = `
        <div class="tool-head">
          <div class="tool-name"><span class="fn">${escapeHtml(m.name || "tool")}</span></div>
          <div class="tool-status">past</div>
        </div>
        <div class="tool-body">
          <pre>${escapeHtml((m.content || "").slice(0, 2000))}</pre>
        </div>`;
      thread.appendChild(card);
    }
  }
  scrollDown();
}

async function refreshJobs() {
  const list = $("jobsList");
  list.innerHTML = '<li class="row-meta">loading…</li>';
  try {
    const { jobs } = await (await fetch("/api/jobs")).json();
    if (!jobs.length) {
      list.innerHTML = '<li class="row-meta">no recurring jobs</li>';
      return;
    }
    list.innerHTML = "";
    for (const j of jobs) {
      const li = document.createElement("li");
      const next = j.next_run ? new Date(j.next_run * 1000).toLocaleString() : "—";
      const target = j.kind === "shell" ? `$ ${j.command}` : `💬 ${j.prompt}`;
      li.innerHTML = `
        <span class="row-title">${escapeHtml(j.name)} ${j.enabled ? "" : "(paused)"}</span>
        <span class="row-meta">
          <span>${escapeHtml(j.when)} · next ${escapeHtml(next)}</span>
          <span>
            <button data-toggle="${j.id}">${j.enabled ? "pause" : "resume"}</button>
            <button data-del="${j.id}">✕</button>
          </span>
        </span>
        <span class="row-meta"><span>${escapeHtml(target.slice(0, 70))}</span></span>`;
      li.querySelector("[data-del]").addEventListener("click", async (e) => {
        e.stopPropagation();
        await fetch(`/api/jobs/${j.id}`, { method: "DELETE" });
        refreshJobs();
      });
      li.querySelector("[data-toggle]").addEventListener("click", async (e) => {
        e.stopPropagation();
        await fetch(`/api/jobs/${j.id}/toggle?enabled=${!j.enabled}`, { method: "POST" });
        refreshJobs();
      });
      list.appendChild(li);
    }
  } catch (e) {
    list.innerHTML = `<li class="row-meta">${escapeHtml(e.message)}</li>`;
  }
}

async function refreshMemory() {
  // The agent stores memory; we surface it via the tool endpoint by
  // calling /api/chat is overkill — call /api/jobs? No: easier to use
  // a small helper. We just read it via /api/conversations isn't right
  // either. Use a dedicated capabilities endpoint? Easiest: call the
  // tool through a tiny one-shot dummy by hitting the memory file via
  // /api/memory.
  const list = $("memoryList");
  list.innerHTML = '<li class="row-meta">loading…</li>';
  try {
    const r = await fetch("/api/memory");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { entries } = await r.json();
    if (!Object.keys(entries).length) {
      list.innerHTML = '<li class="row-meta">no memory yet — say "remember that…"</li>';
      return;
    }
    list.innerHTML = "";
    for (const [k, v] of Object.entries(entries)) {
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="row-title"><strong>${escapeHtml(k)}</strong></span>
        <span class="row-meta">
          <span>${escapeHtml(v.value)}</span>
          <button data-key="${escapeHtml(k)}">✕</button>
        </span>`;
      li.querySelector("button").addEventListener("click", async (e) => {
        e.stopPropagation();
        await fetch(`/api/memory/${encodeURIComponent(k)}`, { method: "DELETE" });
        refreshMemory();
      });
      list.appendChild(li);
    }
  } catch (e) {
    list.innerHTML = `<li class="row-meta">${escapeHtml(e.message)}</li>`;
  }
}

// ── Voice (mic + TTS) ────────────────────────────────────────────────

async function toggleRecording() {
  if (state.recording) return stopRecording();
  if (!navigator.mediaDevices?.getUserMedia) {
    showError("Microphone access not available in this browser.");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
    const chunks = [];
    rec.ondataavailable = (e) => e.data && chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: mime || "audio/webm" });
      await sendVoice(blob);
    };
    rec.start();
    state.recorder = rec;
    state.recording = true;
    micBtn.classList.add("recording");
    micBtn.title = "Click to stop recording";
  } catch (e) {
    showError(`Microphone error: ${e.message}`);
  }
}

function stopRecording() {
  if (state.recorder && state.recording) {
    state.recorder.stop();
    state.recording = false;
    micBtn.classList.remove("recording");
    micBtn.title = "Hold to record (or click to toggle)";
  }
}

async function sendVoice(blob) {
  const fd = new FormData();
  fd.append("file", blob, "input.webm");
  input.disabled = true;
  input.placeholder = "transcribing…";
  try {
    const res = await fetch("/api/voice/transcribe", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    input.value = (input.value ? input.value + " " : "") + (data.text || "");
  } catch (e) {
    showError(`Transcription failed: ${e.message}`);
  } finally {
    input.disabled = false;
    input.placeholder = "Tell me what to do on your laptop…";
    input.focus();
  }
}

async function speakText(text) {
  if (!text) return;
  try {
    const res = await fetch("/api/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    if (state.audioPlayer) state.audioPlayer.pause();
    state.audioPlayer = new Audio(url);
    state.audioPlayer.play().catch(() => {});
  } catch { /* silent */ }
}

// ── Attachments ──────────────────────────────────────────────────────

async function uploadAttachment(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.detail || "upload failed");
    state.attachments.push({
      name: data.name || file.name,
      data_url: data.data_url,
      path: data.path,
      mime: data.mime,
    });
    renderAttachments();
  } catch (e) {
    showError(`Upload failed: ${e.message}`);
  }
}

function renderAttachments() {
  attachStrip.classList.toggle("hidden", state.attachments.length === 0);
  attachStrip.innerHTML = "";
  state.attachments.forEach((a, idx) => {
    const chip = document.createElement("div");
    chip.className = "attach-chip";
    chip.innerHTML = (a.data_url ? `<img src="${a.data_url}" />` : "📎") +
      `<span>${escapeHtml(a.name)}</span><button title="Remove">✕</button>`;
    chip.querySelector("button").addEventListener("click", () => {
      state.attachments.splice(idx, 1);
      renderAttachments();
    });
    attachStrip.appendChild(chip);
  });
}

// ── Chat submit & SSE handling ───────────────────────────────────────

async function submit() {
  if (state.busy) return;
  const text = input.value.trim();
  if (!text && !state.attachments.length && !state.pendingConfirmations) return;

  if (text || state.attachments.length) {
    addUserMessage(text || "(image attached)", state.attachments.map((a) => a.data_url));
  }
  const images = state.attachments.map((a) => a.data_url).filter(Boolean);
  state.attachments = [];
  renderAttachments();
  input.value = "";

  await runRequest({
    message: text || "(image only)",
    model: modelPicker.value,
    safety_mode: state.safety,
    confirmations: {},
    reset: false,
    images,
    speak: speakToggle.checked,
  });
}

async function runRequest(payload, extraConfirmations = {}) {
  state.busy = true;
  sendBtn.disabled = true;
  payload.confirmations = { ...payload.confirmations, ...extraConfirmations };

  const placeholder = addAssistantMessage("");
  placeholder.querySelector(".body").innerHTML = '<span class="typing">thinking…</span>';

  let res;
  try {
    res = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-conversation-id": state.conversationId,
      },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    showError(`Network error: ${e.message}`);
    cleanup();
    return;
  }

  if (!res.ok) {
    let err = await res.text();
    try { err = JSON.parse(err).detail || err; } catch {}
    showError(err);
    placeholder.remove();
    cleanup();
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let placeholderUsed = false;
  let currentAssistant = placeholder;
  let lastFinal = "";

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
          const badge = document.createElement("div");
          badge.className = "model-badge";
          badge.innerHTML = `<span>via <span class="via">${evt.data.via}</span></span> · <strong>${evt.data.model}</strong> · ${escapeHtml(evt.data.reason)}`;
          thread.appendChild(badge);
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
            lastFinal = evt.data;
          }
          break;

        case "tool_call":
          if (!placeholderUsed && currentAssistant) {
            currentAssistant.remove();
            currentAssistant = null;
          }
          renderToolCall(evt.data);
          break;

        case "tool_result":
          updateToolResult(evt.data);
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
            lastFinal = evt.data;
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

  if (currentAssistant && !placeholderUsed && currentAssistant.querySelector(".typing")) {
    currentAssistant.remove();
  }

  if (payload.speak && lastFinal) speakText(lastFinal);

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
  try { data = JSON.parse(raw); } catch { data = raw; }
  return { event: evt, data };
}

// ── Rendering helpers ────────────────────────────────────────────────

function addUserMessage(text, images = []) {
  const el = renderMessage("user", "you", text);
  if (images.length) {
    const body = el.querySelector(".body");
    for (const url of images) {
      const img = document.createElement("img");
      img.className = "preview-img";
      img.src = url;
      body.appendChild(img);
    }
  }
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
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(stripPreview(data.result), null, 2);
  body.appendChild(pre);

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
    </div>`;
  thread.appendChild(card);

  const respond = (approve) => {
    const cs = {};
    for (const c of calls) cs[c.id] = approve;
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    card.remove();
    state.pendingConfirmations = null;
    runRequest({
      message: approve ? "(approved)" : "(denied)",
      model: modelPicker.value,
      safety_mode: state.safety,
      confirmations: cs,
      reset: false,
      images: [],
      speak: speakToggle.checked,
    });
  };
  card.querySelector(".approve").addEventListener("click", () => respond(true));
  card.querySelector(".deny").addEventListener("click", () => respond(false));
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

function timeAgo(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function scrollDown() {
  requestAnimationFrame(() => thread.scrollTo({ top: thread.scrollHeight }));
}

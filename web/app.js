// Mistral Laptop Assistant — frontend
// Handles SSE chat, voice in/out, image attachments, tabs (history /
// jobs / memory) and conversation persistence.

const $ = (id) => document.getElementById(id);

const thread       = $("thread");
const composer     = $("composer");
const input        = $("input");
const sendBtn      = $("sendBtn");
const modelPicker  = $("modelPicker");
const newChatBtn   = $("newChat");
const statusDot    = $("statusDot");
const statusText   = $("statusText");
const sectionNav   = document.querySelector(".section-nav");
const safetyBtns   = document.querySelector(".safety-btns");
const micBtn       = $("micBtn");
const attachBtn    = $("attachBtn");
const fileInput    = $("fileInput");
const attachStrip  = $("attachStrip");
const speakToggle  = $("speakToggle");
const modelChip    = $("modelChip");
const systemInfo   = $("systemInfo");
const thinkingToggle = $("thinkingToggle");
const thinkingLabel  = $("thinkingLabel");
const stopBtn        = $("stopBtn");

const state = {
  conversationId: crypto.randomUUID(),
  safety: "normal",
  persona: "jarvis",  // "jarvis" or "veronica"
  pendingConfirmations: null,
  busy: false,
  abortController: null,  // for cancelling in-flight SSE
  modelsById: {},
  capabilities: { voice: { stt: false, tts: false } },
  attachments: [],          // {name, data_url}
  recorder: null,
  recording: false,
  audioPlayer: null,
  thinkingVisible: true,    // show status events in #thinkingBar
};

// ── Boot ─────────────────────────────────────────────────────────────

(async () => {
  await Promise.all([loadStatus(), loadModels(), loadCapabilities()]);
  bindUI();
})();

async function loadStatus() {
  try {
    const data = await (await fetch("/api/status")).json();
    setStatus(data.api_key_configured ? "ok" : "err",
      data.api_key_configured
        ? `online · ${shorten(data.platform)}`
        : "no API key — set MLA_MISTRAL_API_KEY");
    if (systemInfo) systemInfo.textContent = shorten(data.platform);
    if (data.default_persona) {
      state.persona = data.default_persona;
      document.body.dataset.persona = data.default_persona;
      document.querySelectorAll(".persona-btn").forEach((b) =>
        b.classList.toggle("active", b.dataset.persona === data.default_persona)
      );
    }
  } catch {
    setStatus("err", "server unreachable");
  }
}

function setStatus(kind, label) {
  if (statusDot) statusDot.className = "status-dot pulse-soft " + kind;
  if (statusText) statusText.textContent = label;
}

function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 240) + "px";
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
    if (m.description) opt.title = m.description;
    modelPicker.appendChild(opt);
  }
  modelPicker.value = "auto";
  updateModelChip();
}

function fillSettings() { /* legacy stub — kept for compatibility */ }

function bindUI() {
  modelPicker.addEventListener("change", updateModelChip);

  safetyBtns.querySelectorAll(".safety-btn").forEach((b) => {
    b.addEventListener("click", () => {
      safetyBtns.querySelectorAll(".safety-btn").forEach((x) => x.classList.remove("active"));
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
    // Enter without modifier inserts newline — do NOT submit the form.
    if (e.key === "Enter" && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      // Insert newline at cursor, grow textarea if needed.
      const start = input.selectionStart;
      const end = input.selectionEnd;
      const before = input.value.substring(0, start);
      const after = input.value.substring(end);
      input.value = before + "\n" + after;
      input.selectionStart = input.selectionEnd = start + 1;
      autoGrow(input);
    }
  });

  input.addEventListener("input", () => autoGrow(input));

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

  // Thinking visibility toggle
  thinkingToggle.addEventListener("click", () => {
    state.thinkingVisible = !state.thinkingVisible;
    thinkingToggle.classList.toggle("active", state.thinkingVisible);
    thinkingLabel.textContent = state.thinkingVisible ? "Thinking" : "Silent";
    if (state.thinkingVisible) {
      $("thinkingBar").textContent = "";
    }
  });

  // Stop button — aborts the in-flight SSE request
  stopBtn.addEventListener("click", () => {
    if (state.abortController) {
      state.abortController.abort();
    }
  });

  // Quick-action cards in the sidebar
  document.querySelectorAll("#examples .quick-card").forEach((card) => {
    card.addEventListener("click", () => {
      input.value = card.dataset.q || card.textContent.trim();
      input.focus();
    });
  });

  // Persona switcher
  document.querySelectorAll(".persona-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = btn.dataset.persona;
      state.persona = p;
      document.querySelectorAll(".persona-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.body.dataset.persona = p;
      // Refresh any already-rendered assistant avatars.
      const letter = personaLetter(p);
      document.querySelectorAll(".msg.assistant .avatar").forEach((a) => {
        a.textContent = letter;
      });
    });
  });

  // Section nav tabs
  sectionNav.addEventListener("click", (e) => {
    const btn = e.target.closest(".nav-btn");
    if (!btn) return;
    sectionNav.querySelectorAll(".nav-btn").forEach((x) => x.classList.remove("active"));
    btn.classList.add("active");
    const which = btn.dataset.tab;
    document.querySelectorAll(".tab-pane").forEach((p) => {
      p.classList.toggle("hidden", p.dataset.pane !== which);
    });
    if (which === "history")  refreshHistory();
    if (which === "jobs")    refreshJobs();
    if (which === "memory")  refreshMemory();
    if (which === "settings") refreshSettings();
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

function updateModelChip() {
  const id = modelPicker.value;
  const label = id === "auto" ? "auto" : id;
  if (modelChip) {
    const span = modelChip.querySelector(".chip-label");
    if (span) span.textContent = label;
    modelChip.title = (state.modelsById[id] && state.modelsById[id].description) || label;
  }
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

// ── Settings tab (defaults + key pool) ───────────────────────────────

async function refreshSettings() {
  await Promise.all([refreshKeys(), loadSettingsDefaults()]);
  bindSettingsForm();
}

async function refreshKeys() {
  const list = $("keyList");
  const countEl = $("keyCount");
  list.innerHTML = '<li class="key-row muted">loading…</li>';
  try {
    const r = await fetch("/api/keys");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { keys, total } = await r.json();
    countEl.textContent = total === 1 ? "1 key" : `${total} keys`;
    list.innerHTML = "";
    if (!keys.length) {
      list.innerHTML = '<li class="key-row muted">no keys yet — add one below</li>';
      return;
    }
    keys.forEach((k, i) => {
      const isEnv = k.source === "env";
      const li = document.createElement("li");
      li.className = "key-row" + (i === 0 ? " primary" : "");
      const tagPrimary = i === 0 ? '<span class="key-tag primary">primary</span>' : "";
      const tagEnv     = isEnv ? '<span class="key-tag env">env</span>' : "";
      const removeBtn  = isEnv
        ? '<span class="key-locked" title="Defined in .env — edit there to remove">🔒</span>'
        : `<button type="button" class="key-remove" title="Remove" data-id="${escapeHtml(k.id)}">✕</button>`;
      li.innerHTML = `
        <span class="key-meta">
          <span class="key-label">${escapeHtml(k.label || "untitled")}</span>
          ${tagPrimary}${tagEnv}
        </span>
        <span class="key-prefix">${escapeHtml(k.prefix)}</span>
        ${removeBtn}`;
      const btn = li.querySelector("button.key-remove");
      if (btn) btn.addEventListener("click", () => removeKey(k.id, k.label));
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = `<li class="key-row muted">${escapeHtml(e.message)}</li>`;
  }
}

async function removeKey(id, label) {
  if (!confirm(`Remove key "${label || id}"?`)) return;
  const r = await fetch(`/api/keys/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    showError(data.detail || `Could not remove key (HTTP ${r.status})`);
    return;
  }
  await refreshKeys();
  loadStatus();
}

function bindSettingsForm() {
  const form = $("keyForm");
  if (form.dataset.bound) return;
  form.dataset.bound = "1";

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const labelEl = $("keyLabel");
    const valueEl = $("keyValue");
    const key = valueEl.value.trim();
    const label = labelEl.value.trim();
    if (!key) return;
    const btn = form.querySelector("button");
    btn.disabled = true;
    btn.textContent = "Adding…";
    try {
      const r = await fetch("/api/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, label }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      labelEl.value = "";
      valueEl.value = "";
      await refreshKeys();
      loadStatus();
    } catch (err) {
      showError(`Could not add key: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "Add key";
    }
  });

  document.querySelectorAll("#settingsSafety .safety-btn").forEach((b) => {
    b.addEventListener("click", async () => {
      document.querySelectorAll("#settingsSafety .safety-btn").forEach(
        (x) => x.classList.remove("active")
      );
      b.classList.add("active");
      await saveSettings({ safety_mode: b.dataset.mode });
    });
  });

  $("settingsTts").addEventListener("change", (e) =>
    saveSettings({ tts_enabled: e.target.checked })
  );
  $("settingsModel").addEventListener("change", (e) =>
    saveSettings({ default_model: e.target.value })
  );
}

async function loadSettingsDefaults() {
  const settingsRes = await (await fetch("/api/settings")).json();

  // Persona radios
  const personaWrap = $("settingsPersona");
  personaWrap.innerHTML = "";
  for (const p of settingsRes.personas || []) {
    const id = `sp-${p.id}`;
    const wrap = document.createElement("label");
    wrap.className = "persona-radio-item";
    wrap.htmlFor = id;
    wrap.innerHTML = `
      <input type="radio" name="settingsPersona" id="${id}" value="${escapeHtml(p.id)}"
        ${p.id === settingsRes.default_persona ? "checked" : ""}/>
      <span class="prr-icon">${p.icon || "•"}</span>
      <span class="prr-text">
        <span class="prr-label">${escapeHtml(p.label)}</span>
        <span class="prr-sub">${escapeHtml(p.sub || "")}</span>
      </span>`;
    wrap.querySelector("input").addEventListener("change", async (e) => {
      if (!e.target.checked) return;
      await saveSettings({ default_persona: p.id });
      state.persona = p.id;
      document.body.dataset.persona = p.id;
      document.querySelectorAll(".persona-btn").forEach((b) =>
        b.classList.toggle("active", b.dataset.persona === p.id)
      );
    });
    personaWrap.appendChild(wrap);
  }

  // Default model dropdown — reuse modelsById from sidebar load
  const modelSel = $("settingsModel");
  modelSel.innerHTML = "";
  for (const m of Object.values(state.modelsById)) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.id === "auto" ? "🤖 auto" : m.id;
    if (m.description) opt.title = m.description;
    if (m.id === settingsRes.default_model) opt.selected = true;
    modelSel.appendChild(opt);
  }

  // Safety pills
  document.querySelectorAll("#settingsSafety .safety-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === settingsRes.safety_mode)
  );

  // TTS toggle
  $("settingsTts").checked = !!settingsRes.tts_enabled;
}

async function saveSettings(patch) {
  try {
    const r = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    showError(`Settings save failed: ${e.message}`);
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
  state.abortController = new AbortController();
  stopBtn.classList.remove("hidden");
  stopBtn.classList.add("active");
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
      signal: state.abortController.signal,
    });
  } catch (e) {
    showError(`Network error: ${e.message}`);
    stopBtn.classList.add("hidden");
    stopBtn.classList.remove("active");
    cleanup();
    return;
  }

  if (!res.ok) {
    let err = await res.text();
    try { err = JSON.parse(err).detail || err; } catch {}
    showError(err);
    placeholder.remove();
    stopBtn.classList.add("hidden");
    stopBtn.classList.remove("active");
    cleanup();
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentAssistant = null;  // null until first text event — tool cards go first
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
          if (state.thinkingVisible) {
            $("thinkingBar").textContent = evt.data;
          }
          if (currentAssistant) {
            currentAssistant.querySelector(".body").innerHTML =
              `<span class="typing">${escapeHtml(evt.data)}</span>`;
          }
          break;

        case "message":
          if (state.thinkingVisible) {
            $("thinkingBar").textContent = "";
          }
          if (currentAssistant) {
            currentAssistant.querySelector(".body").innerHTML = renderMarkdown(evt.data);
            lastFinal = evt.data;
            if (evt.speaker) {
              currentAssistant.querySelector(".avatar").textContent = personaLetter(evt.speaker);
            }
          } else {
            currentAssistant = addAssistantMessage(evt.data);
            if (evt.speaker) {
              currentAssistant.querySelector(".avatar").textContent = personaLetter(evt.speaker);
            }
          }
          break;

        case "tool_call":
          if (state.thinkingVisible) {
            $("thinkingBar").textContent = `⚙ ${evt.data.name}(${JSON.stringify(evt.data.arguments)})`;
          }
          if (currentAssistant && !currentAssistant.classList.contains("tool-card")) {
            currentAssistant.remove();
          }
          currentAssistant = null;  // next message creates a fresh bubble
          renderToolCall(evt.data);
          break;

        case "tool_result":
          if (state.thinkingVisible) {
            $("thinkingBar").textContent = "processing…";
          }
          updateToolResult(evt.data);
          // Show a "processing result…" bubble while we wait for synthesis.
          if (!currentAssistant) {
            currentAssistant = addAssistantMessage("");
            currentAssistant.querySelector(".body").innerHTML =
              `<span class="typing">processing result…</span>`;
          }
          break;

        case "confirmation_needed":
          if (currentAssistant) {
            currentAssistant.remove();
            currentAssistant = null;
          }
          renderConfirmation(evt.data);
          state.pendingConfirmations = { calls: evt.data };
          break;

        case "final":
          if (state.thinkingVisible) {
            $("thinkingBar").textContent = "";
          }
          if (currentAssistant) {
            currentAssistant.querySelector(".body").innerHTML = renderMarkdown(evt.data);
            lastFinal = evt.data;
            if (evt.speaker) {
              currentAssistant.querySelector(".avatar").textContent = personaLetter(evt.speaker);
            }
          } else {
            currentAssistant = addAssistantMessage(evt.data);
            if (evt.speaker) {
              currentAssistant.querySelector(".avatar").textContent = personaLetter(evt.speaker);
            }
          }
          break;

        case "error":
          showError(typeof evt.data === "string" ? evt.data : JSON.stringify(evt.data));
          if (currentAssistant) {
            currentAssistant.remove();
            currentAssistant = null;
          }
          break;

        case "done":
          if (state.thinkingVisible) {
            $("thinkingBar").textContent = "";
          }
          break;
      }
      scrollDown();
    }
  }

  // If we only showed tool cards (no text bubble at all), currentAssistant is null.
  // Do nothing — the tool cards are already in the DOM.

  if (payload.speak && lastFinal) speakText(lastFinal);

  cleanup();
}

function cleanup() {
  state.busy = false;
  sendBtn.disabled = false;
  state.abortController = null;
  stopBtn.classList.add("hidden");
  stopBtn.classList.remove("active");
  if (state.thinkingVisible) {
    $("thinkingBar").textContent = "";
  }
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
  const avatar = role === "user" ? "U" : personaLetter(state.persona);
  el.innerHTML = `
    <div class="avatar">${avatar}</div>
    <div class="bubble">
      <div class="who">${who}</div>
      <div class="body">${renderMarkdown(text)}</div>
    </div>`;
  return el;
}

function personaLetter(persona) {
  if (persona === "veronica") return "V";
  if (persona === "friday")   return "F";
  return "J";
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

  // Auto-expand the card so results are visible immediately.
  card.classList.add("open");
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
      persona: state.persona,
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

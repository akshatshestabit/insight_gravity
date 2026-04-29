/**
 * InsightForge Dashboard — app.js
 *
 * Responsibilities:
 *  1. Tab navigation
 *  2. API health check (GET /)
 *  3. File drag-and-drop + upload → POST /ingest
 *  4. Live progress via GET /ingest/stream/{job_id} (SSE) with 1s polling fallback
 *  5. Chat → POST /chat with reasoning trace display
 *  6. Jobs history panel
 */

const API = "http://localhost:8000";

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  files: [],          // FileList staging
  sessionId: null,    // chat session
  jobs: [],           // job history
  activeJobId: null,
  sseSource: null,
};

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $  = (id) => document.getElementById(id);
const $dot    = $("api-status");
const $dotDot = $dot.querySelector(".dot");
const $dotLbl = $dot.querySelector(".status-label");

// ── API health ────────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch(`${API}/health`, { signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      $dotDot.className = "dot online";
      $dotLbl.textContent = "API online";
    } else throw new Error();
  } catch {
    $dotDot.className = "dot offline";
    $dotLbl.textContent = "API offline";
  }
}
checkHealth();
setInterval(checkHealth, 10_000);

// ── Tab navigation ────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.add("hidden"));
    btn.classList.add("active");
    $(`panel-${btn.dataset.tab}`).classList.remove("hidden");
    if (btn.dataset.tab === "jobs") renderJobsHistory();
  });
});

// ── File Drag & Drop ──────────────────────────────────────────────────────────
const dropZone  = $("drop-zone");
const fileInput = $("file-input");
const fileList  = $("file-list");
const ingestBtn = $("ingest-btn");

dropZone.addEventListener("click", (e) => {
  if (e.target.tagName !== "LABEL") fileInput.click();
});
["dragover","dragenter"].forEach((ev) => {
  dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
});
["dragleave","drop"].forEach((ev) => {
  dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.remove("drag-over"); });
});
dropZone.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
fileInput.addEventListener("change", () => addFiles(fileInput.files));

function addFiles(fileList_) {
  for (const f of fileList_) {
    if (!state.files.find((x) => x.name === f.name)) state.files.push(f);
  }
  renderFileList();
}

function renderFileList() {
  fileList.innerHTML = "";
  state.files.forEach((f, i) => {
    const icon = f.name.endsWith(".pdf") ? "📄" : f.name.endsWith(".md") ? "📝" : "📃";
    const el = document.createElement("div");
    el.className = "file-item";
    el.innerHTML = `
      <span class="file-icon">${icon}</span>
      <span class="file-name">${f.name}</span>
      <span class="file-size">${formatBytes(f.size)}</span>
      <button class="file-remove" data-i="${i}" title="Remove">✕</button>`;
    fileList.appendChild(el);
  });
  fileList.querySelectorAll(".file-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.files.splice(+btn.dataset.i, 1);
      renderFileList();
    });
  });
  ingestBtn.disabled = state.files.length === 0;
}

// ── Ingest ────────────────────────────────────────────────────────────────────
ingestBtn.addEventListener("click", startIngest);

async function startIngest() {
  if (state.files.length === 0) return;

  const formData = new FormData();
  state.files.forEach((f) => formData.append("files", f));
  formData.append("mode", $("ingest-mode").value);

  ingestBtn.disabled = true;
  ingestBtn.innerHTML = `<span class="btn-icon">⏳</span> Queuing…`;

  try {
    const res = await fetch(`${API}/ingest`, { method: "POST", body: formData });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    showActiveJob(data.job_id, data.total);
    state.jobs.unshift({ job_id: data.job_id, total: data.total, started: new Date().toISOString() });
    state.files = [];
    renderFileList();
    startSSE(data.job_id);
  } catch (e) {
    alert(`Ingest failed: ${e.message}`);
  } finally {
    ingestBtn.innerHTML = `<span class="btn-icon">🚀</span> Start Ingestion`;
  }
}

function showActiveJob(jobId, total) {
  $("active-job").classList.remove("hidden");
  $("current-job-id").textContent = jobId;
  $("artifact-grid").innerHTML = "";
  setJobBadge("queued");
  updateProgress(0, 0, total);
  state.activeJobId = jobId;
}

function startSSE(jobId) {
  if (state.sseSource) state.sseSource.close();
  const src = new EventSource(`${API}/ingest/stream/${jobId}`);
  state.sseSource = src;
  src.onmessage = (e) => {
    const d = JSON.parse(e.data);
    applyJobUpdate(d);
    if (["done", "failed"].includes(d.status)) src.close();
  };
  src.onerror = () => {
    src.close();
    // Fallback: poll every 2s
    const timer = setInterval(async () => {
      const d = await fetchStatus(jobId);
      if (!d) return;
      applyJobUpdate(d);
      if (["done", "failed"].includes(d.status)) clearInterval(timer);
    }, 2000);
  };
}

async function fetchStatus(jobId) {
  try {
    const r = await fetch(`${API}/ingest/status/${jobId}`);
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

function applyJobUpdate(d) {
  const pct = Math.round((d.progress || 0) * 100);
  updateProgress(pct, d.processed_documents || 0, d.total_documents || 0);
  setJobBadge(d.status);
  renderArtifacts(d.results || []);

  // Sync to job history
  const job = state.jobs.find((j) => j.job_id === d.job_id);
  if (job) Object.assign(job, d);
}

function updateProgress(pct, done, total) {
  $("progress-bar").style.width = `${pct}%`;
  $("progress-text").textContent = `${pct}%`;
  $("docs-text").textContent = `${done} / ${total} documents`;
}

function setJobBadge(status) {
  const el = $("job-badge");
  el.textContent = status;
  el.className = `badge badge-${status}`;
}

function renderArtifacts(results) {
  const grid = $("artifact-grid");
  grid.innerHTML = "";
  results.forEach((r) => {
    const card = document.createElement("div");
    card.className = "artifact-card";
    card.innerHTML = `
      <div class="artifact-name" title="${r.filename}">${r.filename}</div>
      <div class="artifact-stat">📝 Text chunks: <span>${r.text_chunks ?? 0}</span></div>
      <div class="artifact-stat">📊 Tables: <span>${r.tables ?? 0}</span></div>
      <div class="artifact-stat">🖼️ Images: <span>${r.images ?? 0}</span></div>
      ${r.error ? `<div class="artifact-stat" style="color:var(--red)">⚠️ ${r.error}</div>` : ""}`;
    grid.appendChild(card);
  });
}

// ── Chat ──────────────────────────────────────────────────────────────────────
const chatForm   = $("chat-form");
const chatInput  = $("chat-input");
const chatWindow = $("chat-window");

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = chatInput.value.trim();
  if (!msg) return;
  chatInput.value = "";

  appendMessage("user", msg, []);
  const loadingEl = appendLoading();

  try {
    const res = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg, session_id: state.sessionId }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    state.sessionId = data.session_id;
    loadingEl.remove();
    appendMessage("agent", data.answer, data.trace);
  } catch (err) {
    loadingEl.remove();
    appendMessage("agent", `⚠️ Error: ${err.message}`, []);
  }
});

function appendMessage(role, text, trace) {
  // Remove welcome screen
  const welcome = chatWindow.querySelector(".chat-welcome");
  if (welcome) welcome.remove();

  const msg = document.createElement("div");
  msg.className = `chat-msg ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  bubble.textContent = text;
  msg.appendChild(bubble);

  if (trace && trace.length > 0) {
    const traceContainer = document.createElement("div");
    traceContainer.className = "trace-container";

    const steps = trace.filter((s) => ["action","observation","thought"].includes(s.type));
    if (steps.length > 0) {
      const toggle = document.createElement("button");
      toggle.className = "trace-toggle";
      toggle.textContent = `🔍 Show reasoning trace (${steps.length} steps)`;

      const stepsEl = document.createElement("div");
      stepsEl.className = "trace-steps hidden";
      steps.forEach((s) => {
        const step = document.createElement("div");
        step.className = `trace-step ${s.type}`;
        const label = { action: "🔧 Action", observation: "👁 Observation", thought: "💭 Thought" }[s.type] || s.type;
        const content = typeof s.content === "object" ? JSON.stringify(s.content, null, 2) : s.content;
        step.innerHTML = `<strong>${label}${s.tool ? ` [${s.tool}]` : ""}</strong><br>${escHtml(String(content).slice(0, 400))}`;
        stepsEl.appendChild(step);
      });

      toggle.addEventListener("click", () => {
        const hidden = stepsEl.classList.toggle("hidden");
        toggle.textContent = hidden
          ? `🔍 Show reasoning trace (${steps.length} steps)`
          : `🔼 Hide reasoning trace`;
      });

      traceContainer.appendChild(toggle);
      traceContainer.appendChild(stepsEl);
      msg.appendChild(traceContainer);
    }
  }

  chatWindow.appendChild(msg);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return msg;
}

function appendLoading() {
  const msg = document.createElement("div");
  msg.className = "chat-msg agent";
  msg.innerHTML = `<div class="msg-bubble"><span class="loading-dots"><span></span><span></span><span></span></span></div>`;
  chatWindow.appendChild(msg);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return msg;
}

// ── Jobs History ──────────────────────────────────────────────────────────────
function renderJobsHistory() {
  const list = $("jobs-list");
  if (state.jobs.length === 0) {
    list.innerHTML = `<p class="empty-state">No jobs yet. Start an ingestion to see progress here.</p>`;
    return;
  }
  list.innerHTML = "";
  state.jobs.forEach((j) => {
    const card = document.createElement("div");
    card.className = "job-history-card";
    const pct = Math.round((j.progress || 0) * 100);
    card.innerHTML = `
      <div>
        <div style="font-weight:600">${j.job_id.slice(0, 8)}…</div>
        <div class="job-history-meta">${j.job_id}</div>
        <div class="job-history-meta">${new Date(j.started).toLocaleTimeString()}</div>
      </div>
      <div style="text-align:right">
        <span class="badge badge-${j.status || 'queued'}">${j.status || 'queued'}</span>
        <div class="job-history-stats" style="margin-top:8px">
          <span>Docs: <strong>${j.total_documents || j.total || 0}</strong></span>
          <span>Progress: <strong>${pct}%</strong></span>
        </div>
      </div>`;
    list.appendChild(card);
  });
}

function formatBytes(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 ** 2).toFixed(1)} MB`;
}

function escHtml(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ── Retrieve ──────────────────────────────────────────────────────────────────
const retrieveBtn   = $("retrieve-btn");
const retrieveInput = $("retrieve-input");
const retrieveMeta  = $("retrieve-meta");
const retrieveResults = $("retrieve-results");

const MODALITY_ICON = {
  text: "📝", table: "📊", image: "🖼", graph: "🕸", auto: "🔀"
};
const MODALITY_COLOR = {
  text: "#6366f1", table: "#10b981", image: "#f59e0b", graph: "#ec4899"
};

retrieveBtn.addEventListener("click", runRetrieve);
retrieveInput.addEventListener("keydown", (e) => { if (e.key === "Enter") runRetrieve(); });

async function runRetrieve() {
  const query = retrieveInput.value.trim();
  if (!query) return;

  const modality = $("retrieve-modality").value;
  retrieveBtn.disabled = true;
  retrieveBtn.innerHTML = `<span class="btn-icon">⏳</span> Searching…`;
  retrieveResults.innerHTML = `<div class="retrieve-loading">Routing query and searching across modalities…</div>`;
  retrieveMeta.classList.add("hidden");

  try {
    const res = await fetch(`${API}/retrieve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, modality, top_k: 5 }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    renderRetrieveResults(data);
  } catch (err) {
    retrieveResults.innerHTML = `<div class="retrieve-error">⚠️ ${escHtml(err.message)}</div>`;
  } finally {
    retrieveBtn.disabled = false;
    retrieveBtn.innerHTML = `<span class="btn-icon">🔍</span> Search`;
  }
}

function renderRetrieveResults(data) {
  retrieveMeta.classList.remove("hidden");
  retrieveMeta.innerHTML = `
    <span>Query: <strong>${escHtml(data.query)}</strong></span>
    <span>Modality: <strong>${data.modality_used}</strong></span>
    <span>Results: <strong>${data.total}</strong></span>`;

  if (!data.results || data.results.length === 0) {
    retrieveResults.innerHTML = `<p class="empty-state">No results found. Try ingesting documents first.</p>`;
    return;
  }

  retrieveResults.innerHTML = "";
  data.results.forEach((hit, i) => {
    const icon   = MODALITY_ICON[hit.modality] || "📄";
    const color  = MODALITY_COLOR[hit.modality] || "#6366f1";
    const pct    = Math.round(hit.score * 1000) / 10;
    const prov   = hit.provenance;
    const bbox   = prov.bbox ? ` · bbox: [${prov.bbox.map(v => v.toFixed(0)).join(", ")}]` : "";

    const card = document.createElement("div");
    card.className = "retrieve-hit-card";
    card.innerHTML = `
      <div class="hit-header">
        <span class="modality-badge" style="background:${color}20;color:${color};border-color:${color}40">
          ${icon} ${hit.modality.toUpperCase()}
        </span>
        <span class="hit-score">score ${pct}%</span>
        <span class="hit-rank">#${i + 1}</span>
      </div>
      <div class="hit-content">${escHtml(hit.content)}</div>
      <div class="hit-provenance">
        📎 <strong>${escHtml(prov.filename || "unknown")}</strong>
        · page <strong>${prov.page}</strong>${bbox}
      </div>`;
    retrieveResults.appendChild(card);
  });
}

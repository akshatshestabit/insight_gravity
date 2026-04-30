/**
 * InsightForge Dashboard — app.js  (Day 3)
 *
 * Tabs: Ingest · Chat · Retrieve · Research · Sessions · Jobs
 */

const API = "http://localhost:8000";

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  files:       [],
  sessionId:   null,      // chat session
  jobs:        [],        // ingest job history
  activeJobId: null,
  sseSource:   null,
  researchSessions: [],   // research session history (this tab session)
};

// ── DOM helpers ───────────────────────────────────────────────────────────────
const $  = (id) => document.getElementById(id);

// ── API health ────────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch(`${API}/health`, { signal: AbortSignal.timeout(3000) });
    const dot   = $("api-status").querySelector(".dot");
    const label = $("api-status").querySelector(".status-label");
    if (r.ok) { dot.className = "dot online";  label.textContent = "API online"; }
    else throw new Error();
  } catch {
    const dot   = $("api-status").querySelector(".dot");
    const label = $("api-status").querySelector(".status-label");
    dot.className = "dot offline"; label.textContent = "API offline";
  }
}
checkHealth();
setInterval(checkHealth, 10_000);

// ── Tab navigation ────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t)   => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.add("hidden"));
    btn.classList.add("active");
    $(`panel-${btn.dataset.tab}`).classList.remove("hidden");
    if (btn.dataset.tab === "jobs")     renderJobsHistory();
    if (btn.dataset.tab === "sessions") renderSessionsList();
  });
});

// ── File Drag & Drop ──────────────────────────────────────────────────────────
const dropZone  = $("drop-zone");
const fileInput = $("file-input");
const fileList  = $("file-list");
const ingestBtn = $("ingest-btn");

dropZone.addEventListener("click", (e) => { if (e.target.tagName !== "LABEL") fileInput.click(); });
["dragover","dragenter"].forEach((ev) => {
  dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
});
["dragleave","drop"].forEach((ev) => {
  dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.remove("drag-over"); });
});
dropZone.addEventListener("drop",    (e) => addFiles(e.dataTransfer.files));
fileInput.addEventListener("change", ()  => addFiles(fileInput.files));

function addFiles(fl) {
  for (const f of fl) { if (!state.files.find((x) => x.name === f.name)) state.files.push(f); }
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
    btn.addEventListener("click", () => { state.files.splice(+btn.dataset.i, 1); renderFileList(); });
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
    const res  = await fetch(`${API}/ingest`, { method: "POST", body: formData });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    showActiveJob(data.job_id, data.total);
    state.jobs.unshift({ job_id: data.job_id, total: data.total, started: new Date().toISOString() });
    state.files = [];
    renderFileList();
    startSSE(data.job_id);
    // Reload doc list when job completes
    setTimeout(loadIngestedDocs, 3000);
  } catch (e) { alert(`Ingest failed: ${e.message}`); }
  finally { ingestBtn.innerHTML = `<span class="btn-icon">🚀</span> Start Ingestion`; }
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
    if (["done","failed"].includes(d.status)) src.close();
  };
  src.onerror = () => {
    src.close();
    const timer = setInterval(async () => {
      const d = await fetchStatus(jobId);
      if (!d) return;
      applyJobUpdate(d);
      if (["done","failed"].includes(d.status)) clearInterval(timer);
    }, 2000);
  };
}
async function fetchStatus(jobId) {
  try { const r = await fetch(`${API}/ingest/status/${jobId}`); return r.ok ? await r.json() : null; }
  catch { return null; }
}
function applyJobUpdate(d) {
  const pct = Math.round((d.progress || 0) * 100);
  updateProgress(pct, d.processed_documents || 0, d.total_documents || 0);
  setJobBadge(d.status);
  renderArtifacts(d.results || []);
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

// ── Ingested Documents Library ────────────────────────────────────────────────

async function loadIngestedDocs() {
  const list  = $("ingested-docs-list");
  const empty = $("ingested-empty");
  try {
    const res  = await fetch(`${API}/ingest/jobs`);
    if (!res.ok) throw new Error(await res.text());
    const docs = await res.json();

    list.innerHTML = "";
    if (docs.length === 0) {
      list.innerHTML = `<p class="empty-state">No documents ingested yet. Upload files above to get started.</p>`;
      return;
    }

    docs.forEach((doc) => {
      if (doc.status !== "done" && doc.filename === "(no documents)") return;
      const card = document.createElement("div");
      card.className = "ingested-doc-card";
      card.dataset.jobId = doc.job_id;
      const ext = (doc.filename.split(".").pop() || "").toLowerCase();
      const icon = ext === "pdf" ? "📄" : ext === "md" ? "📝" : ext === "csv" ? "📊" : "📃";
      const statusClass = doc.status === "done" ? "badge-done" : doc.status === "failed" ? "badge-failed" : "badge-processing";
      card.innerHTML = `
        <div class="doc-card-left">
          <span class="doc-icon">${icon}</span>
          <div class="doc-info">
            <div class="doc-name" title="${escHtml(doc.filename)}">${escHtml(doc.filename)}</div>
            <div class="doc-meta">
              <span class="badge ${statusClass}" style="font-size:10px;padding:2px 7px">${doc.status}</span>
              <span>📝 ${doc.text_chunks} text</span>
              <span>📊 ${doc.table_chunks} tables</span>
              <span>🖼️ ${doc.image_chunks} images</span>
            </div>
            <div class="doc-jobid">${doc.job_id}</div>
          </div>
        </div>
        <div class="doc-card-right">
          <button class="btn-copy-id" title="Copy Job ID" data-jid="${doc.job_id}">📋 Copy ID</button>
          <button class="btn-delete-doc" title="Delete document" data-jid="${doc.job_id}">🗑 Delete</button>
        </div>`;

      card.querySelector(".btn-copy-id").addEventListener("click", (e) => {
        const jid = e.currentTarget.dataset.jid;
        navigator.clipboard.writeText(jid).then(() => {
          e.currentTarget.textContent = "✅ Copied";
          setTimeout(() => { e.currentTarget.textContent = "📋 Copy ID"; }, 1500);
        });
      });

      card.querySelector(".btn-delete-doc").addEventListener("click", (e) => {
        const jid  = e.currentTarget.dataset.jid;
        const name = card.querySelector(".doc-name").textContent;
        confirmDelete(jid, name, card);
      });

      list.appendChild(card);
    });

  } catch (err) {
    list.innerHTML = `<p class="empty-state" style="color:var(--red)">⚠️ Could not load documents: ${escHtml(err.message)}</p>`;
  }
}

function confirmDelete(jobId, filename, cardEl) {
  // Inline confirmation inside the card
  const right = cardEl.querySelector(".doc-card-right");
  right.innerHTML = `
    <span class="doc-delete-confirm">Delete <strong>${escHtml(filename)}</strong>?</span>
    <button class="btn btn-danger btn-sm" id="confirm-yes-${jobId}">Yes, delete</button>
    <button class="btn-link" id="confirm-no-${jobId}">Cancel</button>`;

  document.getElementById(`confirm-no-${jobId}`).addEventListener("click", () => loadIngestedDocs());
  document.getElementById(`confirm-yes-${jobId}`).addEventListener("click", async () => {
    cardEl.style.opacity = "0.5";
    cardEl.style.pointerEvents = "none";
    try {
      const res = await fetch(`${API}/ingest/${jobId}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      cardEl.style.animation = "slide-out .25s ease forwards";
      setTimeout(() => { cardEl.remove(); checkEmptyDocs(); }, 250);
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
      cardEl.style.opacity = "1";
      cardEl.style.pointerEvents = "";
      loadIngestedDocs();
    }
  });
}

function checkEmptyDocs() {
  const list = $("ingested-docs-list");
  if (list.children.length === 0) {
    list.innerHTML = `<p class="empty-state">No documents ingested yet. Upload files above to get started.</p>`;
  }
}

// Load on startup and after each ingest
loadIngestedDocs();
$("refresh-docs-btn").addEventListener("click", loadIngestedDocs);

// ── Chat ──────────────────────────────────────────────────────────────────────
$("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("chat-input").value.trim();
  if (!msg) return;
  $("chat-input").value = "";
  appendChatMessage("user", msg, []);
  const loading = appendChatLoading();
  try {
    const res  = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg, session_id: state.sessionId }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    state.sessionId = data.session_id;
    loading.remove();
    appendChatMessage("agent", data.answer, data.trace);
  } catch (err) { loading.remove(); appendChatMessage("agent", `⚠️ Error: ${err.message}`, []); }
});

function appendChatMessage(role, text, trace) {
  const welcome = $("chat-window").querySelector(".chat-welcome");
  if (welcome) welcome.remove();
  const msg    = document.createElement("div"); msg.className = `chat-msg ${role}`;
  const bubble = document.createElement("div"); bubble.className = "msg-bubble"; bubble.textContent = text;
  msg.appendChild(bubble);
  if (trace && trace.length > 0) {
    const steps = trace.filter((s) => ["action","observation","thought"].includes(s.type));
    if (steps.length > 0) {
      const tc     = document.createElement("div"); tc.className = "trace-container";
      const toggle = document.createElement("button"); toggle.className = "trace-toggle";
      toggle.textContent = `🔍 Show reasoning trace (${steps.length} steps)`;
      const stepsEl = document.createElement("div"); stepsEl.className = "trace-steps hidden";
      steps.forEach((s) => {
        const step = document.createElement("div"); step.className = `trace-step ${s.type}`;
        const label = {action:"🔧 Action",observation:"👁 Observation",thought:"💭 Thought"}[s.type]||s.type;
        const content = typeof s.content === "object" ? JSON.stringify(s.content,null,2) : s.content;
        step.innerHTML = `<strong>${label}${s.tool?` [${s.tool}]`:""}</strong><br>${escHtml(String(content).slice(0,400))}`;
        stepsEl.appendChild(step);
      });
      toggle.addEventListener("click", () => {
        const h = stepsEl.classList.toggle("hidden");
        toggle.textContent = h ? `🔍 Show reasoning trace (${steps.length} steps)` : "🔼 Hide reasoning trace";
      });
      tc.appendChild(toggle); tc.appendChild(stepsEl); msg.appendChild(tc);
    }
  }
  $("chat-window").appendChild(msg);
  $("chat-window").scrollTop = $("chat-window").scrollHeight;
  return msg;
}
function appendChatLoading() {
  const msg = document.createElement("div"); msg.className = "chat-msg agent";
  msg.innerHTML = `<div class="msg-bubble"><span class="loading-dots"><span></span><span></span><span></span></span></div>`;
  $("chat-window").appendChild(msg);
  $("chat-window").scrollTop = $("chat-window").scrollHeight;
  return msg;
}

// ── Retrieve ──────────────────────────────────────────────────────────────────
const MODALITY_ICON  = { text:"📝", table:"📊", image:"🖼", graph:"🕸", auto:"🔀" };
const MODALITY_COLOR = { text:"#6366f1", table:"#10b981", image:"#f59e0b", graph:"#ec4899" };

$("retrieve-btn").addEventListener("click", runRetrieve);
$("retrieve-input").addEventListener("keydown", (e) => { if (e.key === "Enter") runRetrieve(); });

async function runRetrieve() {
  const query    = $("retrieve-input").value.trim();
  if (!query) return;
  const modality = $("retrieve-modality").value;
  $("retrieve-btn").disabled = true;
  $("retrieve-btn").innerHTML = `<span class="btn-icon">⏳</span> Searching…`;
  $("retrieve-results").innerHTML = `<div class="retrieve-loading">Routing query and searching across modalities…</div>`;
  $("retrieve-meta").classList.add("hidden");
  try {
    const res  = await fetch(`${API}/retrieve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, modality, top_k: 5 }),
    });
    if (!res.ok) throw new Error(await res.text());
    renderRetrieveResults(await res.json());
  } catch (err) {
    $("retrieve-results").innerHTML = `<div class="retrieve-error">⚠️ ${escHtml(err.message)}</div>`;
  } finally {
    $("retrieve-btn").disabled = false;
    $("retrieve-btn").innerHTML = `<span class="btn-icon">🔍</span> Search`;
  }
}
function renderRetrieveResults(data) {
  $("retrieve-meta").classList.remove("hidden");
  $("retrieve-meta").innerHTML = `
    <span>Query: <strong>${escHtml(data.query)}</strong></span>
    <span>Modality: <strong>${data.modality_used}</strong></span>
    <span>Results: <strong>${data.total}</strong></span>`;
  if (!data.results || data.results.length === 0) {
    $("retrieve-results").innerHTML = `<p class="empty-state">No results found. Try ingesting documents first.</p>`;
    return;
  }
  $("retrieve-results").innerHTML = "";
  data.results.forEach((hit, i) => {
    const icon  = MODALITY_ICON[hit.modality] || "📄";
    const color = MODALITY_COLOR[hit.modality] || "#6366f1";
    const pct   = Math.round(hit.score * 1000) / 10;
    const prov  = hit.provenance;
    const bbox  = prov.bbox ? ` · bbox: [${prov.bbox.map((v) => v.toFixed(0)).join(", ")}]` : "";
    const card  = document.createElement("div"); card.className = "retrieve-hit-card";
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
    $("retrieve-results").appendChild(card);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// ── RESEARCH ──────────────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════

const AGENT_META = {
  planner:       { icon: "🗺️",  label: "Planner",       desc: "Decomposing query into a research plan" },
  retriever:     { icon: "🔍",  label: "Retriever",      desc: "Executing multimodal hybrid retrieval" },
  table_analyst: { icon: "📊",  label: "Table Analyst",  desc: "Analysing tables with Pandas / DuckDB" },
  chart_analyst: { icon: "🖼️", label: "Chart Analyst",  desc: "Interpreting figures with Gemini Vision" },
  synthesizer:   { icon: "✍️",  label: "Synthesizer",    desc: "Composing citation-rich answer" },
  critic:        { icon: "🎯",  label: "Critic",         desc: "Verifying faithfulness & citations" },
  done:          { icon: "✅",  label: "Complete",        desc: "Research complete" },
};

$("research-btn").addEventListener("click", runResearch);

async function runResearch() {
  const query = $("research-query").value.trim();
  if (!query) { alert("Please enter a research query."); return; }

  const jobIdsRaw = $("research-job-ids").value.trim();
  const jobIds    = jobIdsRaw ? jobIdsRaw.split(",").map((s) => s.trim()).filter(Boolean) : [];
  const maxCost   = parseFloat($("research-max-cost").value) || 0.50;
  const hitl      = $("research-hitl").checked;

  // Reset UI
  $("research-btn").disabled = true;
  $("research-btn").innerHTML = `<span class="btn-icon">⏳</span> Running…`;
  $("research-results").innerHTML = "";
  showPipeline(true);
  resetPipelineAgents();

  try {
    // Start SSE stream for live agent events (fire-and-forget placeholder stream)
    let sseSessionId = "pending-" + Date.now();
    startResearchSSE(sseSessionId);

    const res = await fetch(`${API}/research`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        job_ids: jobIds,
        config: { max_cost_usd: maxCost, require_hitl: hitl },
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    // Update SSE source with real session id
    stopResearchSSE();
    markPipelineDone(data);

    // Save to session history
    state.researchSessions.unshift({ ...data, query, ts: new Date().toISOString() });

    renderResearchResults(data);

    // If awaiting HITL approval, show approve/reject buttons
    if (data.awaiting_approval) showHITLPrompt(data.session_id);

  } catch (err) {
    $("research-results").innerHTML = `<div class="retrieve-error">⚠️ ${escHtml(err.message)}</div>`;
    showPipeline(false);
  } finally {
    $("research-btn").disabled = false;
    $("research-btn").innerHTML = `<span class="btn-icon">🔬</span> Run Research`;
  }
}

// ── Pipeline progress UI ───────────────────────────────────────────────────────
let _researchSSE = null;
let _agentOrder  = ["planner","retriever","table_analyst","chart_analyst","synthesizer","critic"];
let _currentAgent = 0;
let _agentTimer   = null;

function showPipeline(visible) {
  $("research-pipeline").classList.toggle("hidden", !visible);
}

function resetPipelineAgents() {
  _currentAgent = 0;
  const container = $("pipeline-agents");
  container.innerHTML = "";
  _agentOrder.forEach((key) => {
    const m   = AGENT_META[key];
    const el  = document.createElement("div");
    el.className = "pipeline-agent pending";
    el.id = `pa-${key}`;
    el.innerHTML = `
      <div class="pa-icon">${m.icon}</div>
      <div class="pa-info">
        <div class="pa-label">${m.label}</div>
        <div class="pa-desc">${m.desc}</div>
      </div>
      <div class="pa-status-icon"></div>`;
    container.appendChild(el);
  });
}

function startResearchSSE(sessionId) {
  stopResearchSSE();
  $("pipeline-session-id").textContent = "";

  // Animate agents sequentially using the SSE stream
  const src = new EventSource(`${API}/research/stream/${sessionId}`);
  _researchSSE = src;

  src.addEventListener("agent_update", (e) => {
    const d = JSON.parse(e.data);
    $("pipeline-session-id").textContent = d.session_id || "";
    setAgentState(d.agent, "running");
    // Mark previous as done
    const prevIdx = _agentOrder.indexOf(d.agent) - 1;
    if (prevIdx >= 0) setAgentState(_agentOrder[prevIdx], "done");
  });

  src.addEventListener("done", () => {
    _agentOrder.forEach((k) => setAgentState(k, "done"));
    src.close();
  });

  src.onerror = () => src.close();

  // Fallback visual ticker (animates even if SSE is a demo stream)
  _agentTimer = setInterval(() => {
    if (_currentAgent < _agentOrder.length) {
      if (_currentAgent > 0) setAgentState(_agentOrder[_currentAgent - 1], "done");
      setAgentState(_agentOrder[_currentAgent], "running");
      _currentAgent++;
    }
  }, 4000);
}

function stopResearchSSE() {
  if (_researchSSE) { _researchSSE.close(); _researchSSE = null; }
  if (_agentTimer)  { clearInterval(_agentTimer); _agentTimer = null; }
}

function setAgentState(key, state_) {
  const el = $(`pa-${key}`);
  if (!el) return;
  el.className = `pipeline-agent ${state_}`;
  const icon = el.querySelector(".pa-status-icon");
  icon.textContent = state_ === "running" ? "⏳" : state_ === "done" ? "✅" : "";
}

function markPipelineDone(data) {
  stopResearchSSE();
  _agentOrder.forEach((k) => setAgentState(k, "done"));
  const sid = data.session_id || "";
  $("pipeline-session-id").textContent = sid ? `Session: ${sid.slice(0,8)}…` : "";
}

// ── Research result renderer ───────────────────────────────────────────────────
function renderResearchResults(data) {
  const container = $("research-results");
  container.innerHTML = "";

  const plan    = data.plan    || {};
  const report  = data.report  || {};
  const crit    = data.critique|| {};
  const answer  = report.answer || "";
  const execSum = report.executive_summary || "";
  const tableA  = report.table_analysis || "";
  const chartA  = report.chart_analysis  || "";
  const cits    = report.citations || [];

  // ── Meta bar ─────────────────────────────────────────────────────────────
  const meta = document.createElement("div"); meta.className = "res-meta-bar";
  meta.innerHTML = `
    <span>⏱ ${(data.duration_ms/1000).toFixed(1)}s</span>
    <span>💰 $${(data.cost_usd||0).toFixed(4)}</span>
    <button class="res-session-chip" title="Click to copy full Session ID" data-sid="${data.session_id||''}">
      🔑 ${(data.session_id||'').slice(0,8)}… <span style="font-size:10px;opacity:.6">copy</span>
    </button>
    <button class="btn-link" style="font-size:12px" data-goto-session="${data.session_id||''}">
      📋 View in Sessions →
    </button>
    ${data.error ? `<span class="res-error-chip">⚠️ ${escHtml(data.error)}</span>` : ""}`;
  // copy on click
  meta.querySelector(".res-session-chip").addEventListener("click", (e) => {
    const sid = e.currentTarget.dataset.sid;
    navigator.clipboard.writeText(sid).then(() => {
      e.currentTarget.innerHTML = `✅ Copied!`;
      setTimeout(() => { e.currentTarget.innerHTML = `🔑 ${sid.slice(0,8)}… <span style="font-size:10px;opacity:.6">copy</span>`; }, 1500);
    });
  });
  // navigate to Sessions tab and pre-fill the lookup
  meta.querySelector("[data-goto-session]").addEventListener("click", (e) => {
    const sid = e.currentTarget.getAttribute("data-goto-session");
    document.querySelector('.tab[data-tab="sessions"]').click();
    $("session-id-input").value = sid;
    lookupSession();
  });
  container.appendChild(meta);

  // ── Plan card ─────────────────────────────────────────────────────────────
  if (plan.steps && plan.steps.length) {
    const card = mkCard("📋 Research Plan");
    const flags = [
      plan.requires_table_analysis ? "📊 Table Analysis" : null,
      plan.requires_chart_analysis ? "🖼️ Chart Analysis" : null,
    ].filter(Boolean);
    if (flags.length) {
      const flagEl = document.createElement("div"); flagEl.className = "plan-flags";
      flags.forEach((f) => { const b = document.createElement("span"); b.className = "plan-flag"; b.textContent = f; flagEl.appendChild(b); });
      card.querySelector(".res-card-body").appendChild(flagEl);
    }
    const ol = document.createElement("ol"); ol.className = "plan-steps";
    plan.steps.forEach((s) => { const li = document.createElement("li"); li.textContent = s; ol.appendChild(li); });
    card.querySelector(".res-card-body").appendChild(ol);
    container.appendChild(card);
  }

  // ── Executive Summary ─────────────────────────────────────────────────────
  if (execSum) {
    const card = mkCard("⚡ Executive Summary");
    const p = document.createElement("p"); p.className = "res-exec-summary"; p.textContent = execSum;
    card.querySelector(".res-card-body").appendChild(p);
    container.appendChild(card);
  }

  // ── Main Answer ───────────────────────────────────────────────────────────
  if (answer && !answer.startsWith("Could not")) {
    const card = mkCard("📝 Answer");
    const body = card.querySelector(".res-card-body");
    // Render answer as formatted paragraphs
    answer.split(/\n\n+/).forEach((para) => {
      if (!para.trim()) return;
      const p = document.createElement("p"); p.className = "res-answer-para";
      p.innerHTML = escHtml(para).replace(/\[(\d+)\]/g, '<span class="citation-ref">[$1]</span>');
      body.appendChild(p);
    });
    container.appendChild(card);
  } else if (answer) {
    const card = mkCard("📝 Answer");
    card.querySelector(".res-card-body").innerHTML =
      `<p class="res-empty-note">⚠️ ${escHtml(answer)}</p>`;
    container.appendChild(card);
  }

  // ── Table Analysis ────────────────────────────────────────────────────────
  if (tableA && !tableA.startsWith("No structured")) {
    const card = mkCard("📊 Table Analysis");
    const pre = document.createElement("pre"); pre.className = "res-preformatted";
    pre.textContent = tableA;
    card.querySelector(".res-card-body").appendChild(pre);
    container.appendChild(card);
  }

  // ── Chart Analysis ────────────────────────────────────────────────────────
  if (chartA && !chartA.startsWith("No chart")) {
    const card = mkCard("🖼️ Chart & Figure Analysis");
    const p = document.createElement("p"); p.className = "res-answer-para"; p.textContent = chartA;
    card.querySelector(".res-card-body").appendChild(p);
    container.appendChild(card);
  }

  // ── Citations ─────────────────────────────────────────────────────────────
  if (cits.length > 0) {
    const card = mkCard(`📎 Citations (${cits.length})`);
    const list = document.createElement("div"); list.className = "citations-list";
    cits.forEach((c, i) => {
      const row = document.createElement("div"); row.className = "citation-row";
      const mod = c.modality || "text";
      const color = MODALITY_COLOR[mod] || "#6366f1";
      row.innerHTML = `
        <span class="citation-num">[${i+1}]</span>
        <span class="modality-badge" style="background:${color}20;color:${color};border-color:${color}40;font-size:11px;padding:2px 7px">
          ${MODALITY_ICON[mod]||"📄"} ${mod.toUpperCase()}
        </span>
        <div class="citation-detail">
          <span class="citation-file">${escHtml(c.filename||c.source||"?")}</span>
          <span class="citation-page">p.${c.page||"?"}</span>
          <span class="citation-score">score ${((c.relevance_score||0)*100).toFixed(0)}%</span>
        </div>
        <div class="citation-preview">${escHtml((c.content_preview||"").slice(0,120))}</div>`;
      list.appendChild(row);
    });
    card.querySelector(".res-card-body").appendChild(list);
    container.appendChild(card);
  }

  // ── Critique ─────────────────────────────────────────────────────────────
  if (crit && typeof crit.score === "number") {
    const card = mkCard("🎯 Critique");
    const passed = crit.passed;
    const score  = (crit.score * 100).toFixed(0);
    const body   = card.querySelector(".res-card-body");
    body.innerHTML = `
      <div class="critique-row">
        <div class="critique-score-wrap">
          <div class="critique-score-ring ${passed ? 'pass' : 'fail'}">
            <span class="critique-score-num">${score}%</span>
          </div>
          <span class="critique-verdict ${passed ? 'pass' : 'fail'}">${passed ? "PASSED ✅" : "FAILED ❌"}</span>
        </div>
        <div class="critique-flags">
          <span class="crit-flag ${crit.faithful?'ok':'warn'}">
            ${crit.faithful?"✅":"⚠️"} Faithful
          </span>
          <span class="crit-flag ${crit.citations_valid?'ok':'warn'}">
            ${crit.citations_valid?"✅":"⚠️"} Citations valid
          </span>
        </div>
      </div>`;
    if (crit.issues && crit.issues.length) {
      const ul = document.createElement("ul"); ul.className = "critique-issues";
      crit.issues.forEach((iss) => { const li = document.createElement("li"); li.textContent = iss; ul.appendChild(li); });
      body.appendChild(ul);
    }
    if (crit.suggestions && crit.suggestions.length) {
      const ul = document.createElement("ul"); ul.className = "critique-suggestions";
      crit.suggestions.forEach((s) => { const li = document.createElement("li"); li.textContent = s; ul.appendChild(li); });
      body.appendChild(ul);
    }
    container.appendChild(card);
  }
}

function mkCard(title) {
  const card = document.createElement("div"); card.className = "res-card";
  card.innerHTML = `
    <div class="res-card-header">${escHtml(title)}</div>
    <div class="res-card-body"></div>`;
  return card;
}

// ── HITL approve/reject ────────────────────────────────────────────────────────
function showHITLPrompt(sessionId) {
  const banner = document.createElement("div"); banner.className = "hitl-banner";
  banner.innerHTML = `
    <span class="hitl-icon">⏸️</span>
    <div class="hitl-text">
      <strong>Awaiting human approval</strong>
      <span>The crew is paused before synthesis. Review the plan and retrieved evidence, then approve or reject.</span>
    </div>
    <div class="hitl-actions">
      <button class="btn btn-success" id="hitl-approve">✅ Approve</button>
      <button class="btn btn-danger"  id="hitl-reject">❌ Reject</button>
    </div>`;
  $("research-results").insertBefore(banner, $("research-results").firstChild);

  $("hitl-approve").addEventListener("click", () => resumeSession(sessionId, true,  banner));
  $("hitl-reject" ).addEventListener("click", () => resumeSession(sessionId, false, banner));
}

async function resumeSession(sessionId, approved, bannerEl) {
  bannerEl.innerHTML = `<span class="hitl-icon">⏳</span> <span>${approved ? "Resuming…" : "Aborting…"}</span>`;
  try {
    const res = await fetch(`${API}/session/${sessionId}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    bannerEl.remove();
    if (approved && data.final_report) renderResearchResults({ ...data, session_id: sessionId });
    else bannerEl.textContent = "Session aborted.";
  } catch (err) { bannerEl.innerHTML = `⚠️ Resume failed: ${escHtml(err.message)}`; }
}

// ══════════════════════════════════════════════════════════════════════════════
// ── SESSIONS ──────────────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════

$("session-lookup-btn").addEventListener("click", lookupSession);
$("session-id-input").addEventListener("keydown", (e) => { if (e.key === "Enter") lookupSession(); });

async function lookupSession() {
  const sid = $("session-id-input").value.trim();
  if (!sid) return;
  const list = $("sessions-list");
  list.innerHTML = `<div class="retrieve-loading">Looking up session…</div>`;
  try {
    const res = await fetch(`${API}/session/${sid}`);
    if (!res.ok) throw new Error((await res.json()).detail || "Not found");
    const data = await res.json();
    renderSessionCard(data, list, true);
  } catch (err) {
    list.innerHTML = `<div class="retrieve-error">⚠️ ${escHtml(err.message)}</div>`;
  }
}

async function renderSessionsList() {
  const list = $("sessions-list");
  list.innerHTML = `<div class="retrieve-loading">Loading recent sessions…</div>`;
  try {
    const res = await fetch(`${API}/sessions?limit=20`);
    if (!res.ok) throw new Error("Could not load sessions");
    const dbSessions = await res.json();

    // Merge in-memory sessions (have query text) with DB records
    const seen = new Set();
    const merged = [];
    for (const s of state.researchSessions) { seen.add(s.session_id); merged.push(s); }
    for (const s of dbSessions) {
      if (!seen.has(s.session_id)) merged.push({ ...s, ts: s.created_at });
    }

    if (merged.length === 0) {
      list.innerHTML = `<p class="empty-state">No sessions yet. Run a research query to get started.</p>`;
      return;
    }
    list.innerHTML = "";
    merged.forEach((s) => renderSessionCard(s, list, false));
  } catch (err) {
    // Fallback to in-memory only
    if (state.researchSessions.length === 0) {
      list.innerHTML = `<p class="empty-state">No sessions yet. Run a research query to get started.</p>`;
    } else {
      list.innerHTML = "";
      state.researchSessions.forEach((s) => renderSessionCard(s, list, false));
    }
  }
}

function renderSessionCard(s, container, clear) {
  if (clear) container.innerHTML = "";
  const card = document.createElement("div"); card.className = "session-card";
  const status = s.awaiting_approval ? "awaiting_approval"
               : s.error             ? "error"
               : s.final_report      ? "completed"
               : "running";
  const statusLabels = { completed:"done", running:"processing", awaiting_approval:"hitl", error:"failed" };
  card.innerHTML = `
    <div class="session-card-header">
      <div>
        <div class="session-query">${escHtml((s.query||"").slice(0,120))}${(s.query||"").length>120?"…":""}</div>
        <code class="job-id">${s.session_id||""}</code>
      </div>
      <span class="badge badge-${statusLabels[status]||status}">${status.replace("_"," ")}</span>
    </div>
    <div class="session-meta">
      <span>⏱ ${s.duration_ms ? (s.duration_ms/1000).toFixed(1)+"s" : "—"}</span>
      <span>💰 $${(s.cost_usd||0).toFixed(4)}</span>
      <span>${s.ts ? new Date(s.ts).toLocaleTimeString() : ""}</span>
    </div>
    <div class="session-actions">
      <button class="btn-link" data-sid="${s.session_id}" data-action="messages">🗒 View message trace</button>
      ${status === "awaiting_approval" ? `<button class="btn btn-success btn-sm" data-sid="${s.session_id}" data-action="resume">▶ Resume</button>` : ""}
    </div>
    <div class="session-trace hidden" id="trace-${s.session_id}"></div>`;
  container.appendChild(card);

  card.querySelector("[data-action='messages']").addEventListener("click", async (e) => {
    const sid   = e.target.dataset.sid;
    const traceDiv = $(`trace-${sid}`);
    if (!traceDiv.classList.contains("hidden")) { traceDiv.classList.add("hidden"); return; }
    traceDiv.innerHTML = `<div class="retrieve-loading">Loading trace…</div>`;
    traceDiv.classList.remove("hidden");
    try {
      const r = await fetch(`${API}/session/${sid}/messages`);
      if (!r.ok) throw new Error("Not found");
      const d = await r.json();
      traceDiv.innerHTML = "";
      if (!d.messages || d.messages.length === 0) {
        traceDiv.innerHTML = `<p class="res-empty-note">No messages yet.</p>`; return;
      }
      d.messages.forEach((m, i) => {
        const row = document.createElement("div"); row.className = "trace-msg-row";
        row.innerHTML = `<span class="trace-msg-type">${escHtml(m.type)}</span>
          <span class="trace-msg-content">${escHtml(String(m.content||"").slice(0,200))}</span>`;
        traceDiv.appendChild(row);
      });
    } catch (err) { traceDiv.innerHTML = `<p class="res-empty-note">⚠️ ${escHtml(err.message)}</p>`; }
  });

  const resumeBtn = card.querySelector("[data-action='resume']");
  if (resumeBtn) {
    resumeBtn.addEventListener("click", async (e) => {
      const sid = e.target.dataset.sid;
      resumeBtn.disabled = true; resumeBtn.textContent = "Resuming…";
      try {
        const r = await fetch(`${API}/session/${sid}/resume`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved: true }),
        });
        const d = await r.json();
        card.querySelector(".badge").textContent = "completed";
        card.querySelector(".badge").className   = "badge badge-done";
        resumeBtn.remove();
      } catch { resumeBtn.textContent = "Failed"; }
    });
  }
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
    const card = document.createElement("div"); card.className = "job-history-card";
    const pct  = Math.round((j.progress || 0) * 100);
    card.innerHTML = `
      <div>
        <div style="font-weight:600">${j.job_id.slice(0,8)}…</div>
        <div class="job-history-meta">${j.job_id}</div>
        <div class="job-history-meta">${new Date(j.started).toLocaleTimeString()}</div>
      </div>
      <div style="text-align:right">
        <span class="badge badge-${j.status||'queued'}">${j.status||'queued'}</span>
        <div class="job-history-stats" style="margin-top:8px">
          <span>Docs: <strong>${j.total_documents||j.total||0}</strong></span>
          <span>Progress: <strong>${pct}%</strong></span>
        </div>
      </div>`;
    list.appendChild(card);
  });
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function formatBytes(b) {
  if (b < 1024)    return `${b} B`;
  if (b < 1024**2) return `${(b/1024).toFixed(1)} KB`;
  return `${(b/1024**2).toFixed(1)} MB`;
}
function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

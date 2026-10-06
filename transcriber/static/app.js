"use strict";

const $ = (id) => document.getElementById(id);
const ACTIVE = new Set(["queued", "running", "uploading"]);

const state = {
  info: null,
  jobs: [],            // summaries from the server, plus local "uploading" placeholders
  uploads: new Map(),  // tempId -> {id, filename, status:'uploading', progress, xhr}
  selected: null,
  segments: [],        // segments of the selected job
  mediaUrls: new Map(),// jobId -> object URL of the local file (only for this browser session)
  detail: null,
};

// ---------------------------------------------------------------- helpers

function clock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? `${h}:` : "") + `${mm}:${String(s).padStart(2, "0")}`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg, ms = 2600) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (el.hidden = true), ms);
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

const PREFS_KEY = "vt-prefs";
function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; }
}
function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      model: $("model").value, language: $("language").value, task: $("task").value, vad: $("vad").checked,
    }));
  } catch {}
}

// ---------------------------------------------------------------- setup

async function init() {
  state.info = await api("/api/info");
  const { info } = state;
  const dev = $("device");
  dev.textContent = info.device === "cuda" ? "GPU accelerated" : "Running on CPU";
  dev.classList.toggle("gpu", info.device === "cuda");

  const prefs = loadPrefs();
  const modelSel = $("model");
  for (const m of info.models) {
    const opt = new Option(m.id + (m.id === info.default_model ? " (recommended)" : ""), m.id);
    opt.title = m.description;
    modelSel.add(opt);
  }
  modelSel.value = info.models.some((m) => m.id === prefs.model) ? prefs.model : info.default_model;
  const langSel = $("language");
  for (const l of info.languages) langSel.add(new Option(l.name, l.code));
  if (prefs.language) langSel.value = prefs.language;
  if (prefs.task) $("task").value = prefs.task;
  if (typeof prefs.vad === "boolean") $("vad").checked = prefs.vad;
  $("file-input").accept = info.extensions.join(",");

  const updateHint = () => {
    const m = info.models.find((x) => x.id === modelSel.value);
    $("model-hint").textContent = m ? m.description + ". Models download automatically the first time they are used." : "";
  };
  modelSel.addEventListener("change", updateHint);
  updateHint();
  for (const id of ["model", "language", "task", "vad"]) $(id).addEventListener("change", savePrefs);

  bindUpload();
  bindViewer();
  await refreshJobs();
  if (state.jobs.length) select(state.jobs[0].id);
  setInterval(tick, 1000);
}

// ---------------------------------------------------------------- uploading

function bindUpload() {
  const dz = $("dropzone"), input = $("file-input");
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
  // Allow dropping anywhere on the page.
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => { e.preventDefault(); if (!dz.contains(e.target)) addFiles(e.dataTransfer.files); });
}

function addFiles(fileList) {
  const exts = new Set(state.info.extensions);
  let first = true;
  for (const file of fileList) {
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!exts.has(ext)) { toast(`Skipped ${file.name}: unsupported file type`); continue; }
    upload(file, first);
    first = false;
  }
}

function upload(file, selectIt) {
  const tempId = "up-" + Math.random().toString(36).slice(2);
  const entry = { id: tempId, filename: file.name, status: "uploading", progress: 0, message: "Uploading", created_at: Date.now() / 1000 };
  state.uploads.set(tempId, entry);
  const mediaUrl = URL.createObjectURL(file);

  const form = new FormData();
  form.append("file", file);
  form.append("model", $("model").value);
  form.append("language", $("language").value);
  form.append("task", $("task").value);
  form.append("vad", $("vad").checked ? "true" : "false");

  const xhr = new XMLHttpRequest();
  entry.xhr = xhr;
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      entry.progress = e.loaded / e.total;
      entry.message = `Uploading ${Math.round(entry.progress * 100)}%`;
      renderJobs();
      if (state.selected === tempId) renderDetail(entry);
    }
  };
  xhr.onload = async () => {
    state.uploads.delete(tempId);
    if (xhr.status >= 200 && xhr.status < 300) {
      const job = JSON.parse(xhr.responseText);
      state.mediaUrls.set(job.id, mediaUrl);
      await refreshJobs();
      if (state.selected === tempId) select(job.id);
    } else {
      URL.revokeObjectURL(mediaUrl);
      let msg = "Upload failed";
      try { msg = JSON.parse(xhr.responseText).detail || msg; } catch {}
      toast(`${file.name}: ${msg}`, 5000);
      if (state.selected === tempId) state.selected = null;
      renderJobs();
      renderDetail(null);
    }
  };
  xhr.onerror = () => {
    state.uploads.delete(tempId);
    URL.revokeObjectURL(mediaUrl);
    toast(`${file.name}: upload failed. Is the app still running?`, 5000);
    renderJobs();
  };
  xhr.send(form);
  renderJobs();
  if (selectIt) select(tempId);
}

// ---------------------------------------------------------------- job list

async function refreshJobs() {
  try {
    state.jobs = await api("/api/jobs");
  } catch {
    return;
  }
  if (state.selected && !state.selected.startsWith("up-") && !state.jobs.some((j) => j.id === state.selected)) {
    state.selected = null;
    renderDetail(null);
  }
  renderJobs();
}

function allJobs() {
  return [...state.uploads.values(), ...state.jobs].sort((a, b) => b.created_at - a.created_at);
}

const STATUS_LABEL = { uploading: "Uploading", queued: "Queued", running: "Working", done: "Done", error: "Failed", cancelled: "Cancelled" };

function jobSubtitle(j) {
  if (j.status === "done") return `${clock(j.duration)} · ${j.detected_language || "?"} · ${new Date(j.finished_at * 1000).toLocaleString()}`;
  if (j.status === "running" || j.status === "uploading") return j.message;
  if (j.status === "error") return j.error || "Failed";
  return new Date(j.created_at * 1000).toLocaleString();
}

function renderJobs() {
  const list = $("job-list");
  const jobs = allJobs();
  $("jobs-empty").hidden = jobs.length > 0;
  list.innerHTML = jobs.map((j) => `
    <li class="job ${j.id === state.selected ? "selected" : ""}" data-id="${j.id}" title="${escapeHtml(j.filename)}">
      <span class="name">${escapeHtml(j.filename)}</span>
      <span class="badge ${j.status}">${STATUS_LABEL[j.status] || j.status}</span>
      <span class="sub">${escapeHtml(jobSubtitle(j))}</span>
      ${ACTIVE.has(j.status) ? `<div class="mini"><div style="width:${Math.round((j.progress || 0) * 100)}%"></div></div>` : ""}
    </li>`).join("");
}

$("job-list").addEventListener("click", (e) => {
  const li = e.target.closest(".job");
  if (li) select(li.dataset.id);
});

// ---------------------------------------------------------------- detail view

function select(id) {
  if (state.selected !== id) {
    state.selected = id;
    state.segments = [];
    state.detail = null;
    $("transcript").innerHTML = "";
    $("search").value = "";
    const player = $("player");
    const url = state.mediaUrls.get(id);
    player.hidden = !url;
    if (url) { if (player.src !== url) player.src = url; } else { player.removeAttribute("src"); player.load(); }
  }
  renderJobs();
  const up = state.uploads.get(id);
  if (up) renderDetail(up); else loadDetail();
}

async function loadDetail() {
  const id = state.selected;
  if (!id || id.startsWith("up-")) return;
  let data;
  try {
    data = await api(`/api/jobs/${id}?since=${state.segments.length}`);
  } catch {
    return;
  }
  if (state.selected !== id) return;
  if (data.segments_offset === state.segments.length) {
    state.segments.push(...data.segments);
    appendSegments(data.segments, data.segments_offset);
  }
  state.detail = data;
  renderDetail(data);
}

function renderDetail(job) {
  $("placeholder").hidden = !!job;
  $("detail").hidden = !job;
  if (!job) return;

  $("d-title").textContent = job.filename;
  const meta = [];
  if (job.duration) meta.push(`Length ${clock(job.duration)}`);
  if (job.detected_language) meta.push(`Language: ${job.detected_language}`);
  if (job.used_model || job.model) meta.push(`Model: ${job.used_model || job.model || "default"}`);
  if (job.task === "translate") meta.push("Translated to English");
  if (job.device) meta.push(job.device.toUpperCase());
  if (job.elapsed) meta.push(`took ${job.elapsed < 60 ? job.elapsed.toFixed(1) + "s" : clock(job.elapsed)}`);
  $("d-meta").textContent = meta.join(" · ");

  const active = ACTIVE.has(job.status);
  $("progress-wrap").hidden = !active;
  if (active) {
    const pct = Math.round((job.progress || 0) * 100);
    const indeterminate = job.status === "queued" || (job.status === "running" && !pct);
    $("progress-wrap").querySelector(".progress").classList.toggle("indeterminate", indeterminate);
    $("progress-bar").style.width = indeterminate ? "" : pct + "%";
    let status = job.message || "";
    if (job.status === "queued" && job.queue_position > 1) status = `Waiting in queue (position ${job.queue_position})`;
    if (job.status === "running" && pct) status += ` · ${pct}%`;
    $("d-status").textContent = status;
  }
  $("btn-cancel").hidden = !active;
  $("btn-delete").hidden = active;

  $("d-error").hidden = job.status !== "error" && job.status !== "cancelled";
  $("d-error").textContent = job.status === "cancelled" ? "This transcription was cancelled." : (job.error || "");

  $("toolbar").hidden = !state.segments.length;
  $("btn-download").disabled = job.status !== "done";
  $("btn-download").title = job.status === "done" ? "" : "Available when transcription finishes";
  if (job.status === "done" && !state.segments.length) {
    $("transcript").innerHTML = '<p class="empty">No speech was detected in this file.</p>';
  }
}

function appendSegments(segs, offset) {
  const box = $("transcript");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  const canSeek = state.mediaUrls.has(state.selected);
  const q = $("search").value.trim().toLowerCase();
  const html = segs.map((s, i) => {
    const text = s.text.trim();
    const hidden = q && !text.toLowerCase().includes(q);
    return `<div class="seg${hidden ? " hidden" : ""}" data-i="${offset + i}">
      <button class="ts" data-t="${s.start}" ${canSeek ? "" : "disabled"} title="${canSeek ? "Jump to this point" : ""}">${clock(s.start)}</button>
      <span class="text">${highlight(text, q)}</span></div>`;
  }).join("");
  box.insertAdjacentHTML("beforeend", html);
  if (nearBottom && ACTIVE.has(state.detail?.status ?? "running")) box.scrollTop = box.scrollHeight;
}

function highlight(text, q) {
  const safe = escapeHtml(text);
  if (!q) return safe;
  const re = new RegExp(escapeHtml(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  return safe.replace(re, (m) => `<mark>${m}</mark>`);
}

function applySearch() {
  const q = $("search").value.trim().toLowerCase();
  document.querySelectorAll("#transcript .seg").forEach((el) => {
    const seg = state.segments[+el.dataset.i];
    const text = seg.text.trim();
    el.classList.toggle("hidden", !!q && !text.toLowerCase().includes(q));
    el.querySelector(".text").innerHTML = highlight(text, q);
  });
}

function plainText() {
  const withTs = $("show-ts").checked;
  return state.segments.map((s) => (withTs ? `[${clock(s.start)}] ` : "") + s.text.trim()).filter(Boolean).join("\n");
}

function bindViewer() {
  $("search").addEventListener("input", applySearch);
  $("show-ts").addEventListener("change", () => $("transcript").classList.toggle("no-ts", !$("show-ts").checked));

  $("btn-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(plainText());
      toast("Transcript copied to clipboard");
    } catch {
      toast("Could not access the clipboard");
    }
  });

  const menu = $("download-menu");
  $("btn-download").addEventListener("click", (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; });
  document.addEventListener("click", () => (menu.hidden = true));
  menu.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-fmt]");
    if (!a || !state.selected) return;
    window.location.href = `/api/jobs/${state.selected}/download/${a.dataset.fmt}`;
  });

  $("btn-cancel").addEventListener("click", async () => {
    const id = state.selected;
    const up = state.uploads.get(id);
    if (up) {
      up.xhr.abort();
      state.uploads.delete(id);
      state.selected = null;
      renderJobs();
      renderDetail(null);
      return;
    }
    try { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); } catch (err) { toast(err.message); }
    tick();
  });

  $("btn-delete").addEventListener("click", async () => {
    const id = state.selected;
    if (!id || !confirm("Delete this transcript? This can't be undone.")) return;
    try { await api(`/api/jobs/${id}`, { method: "DELETE" }); } catch (err) { toast(err.message); return; }
    const url = state.mediaUrls.get(id);
    if (url) { URL.revokeObjectURL(url); state.mediaUrls.delete(id); }
    state.selected = null;
    await refreshJobs();
    const next = allJobs()[0];
    if (next) select(next.id); else renderDetail(null);
  });

  $("transcript").addEventListener("click", (e) => {
    const ts = e.target.closest(".ts");
    if (!ts || ts.disabled) return;
    const player = $("player");
    player.currentTime = +ts.dataset.t;
    player.play();
  });

  // Highlight the segment currently playing.
  $("player").addEventListener("timeupdate", () => {
    const t = $("player").currentTime;
    const idx = state.segments.findIndex((s) => t >= s.start && t < s.end);
    document.querySelectorAll("#transcript .seg.active").forEach((el) => el.classList.remove("active"));
    if (idx >= 0) {
      const el = document.querySelector(`#transcript .seg[data-i="${idx}"]`);
      if (el) {
        el.classList.add("active");
        if (!$("player").paused) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  });
}

// ---------------------------------------------------------------- polling

let ticking = false, idleTicks = 0;
async function tick() {
  if (ticking) return;
  const hasActive = state.jobs.some((j) => ACTIVE.has(j.status));
  const selectedActive = state.detail && ACTIVE.has(state.detail.status);
  // Poll every second while something is running; otherwise every 5s to pick up
  // jobs started elsewhere (another tab or the API).
  if (!hasActive && !selectedActive && ++idleTicks % 5) return;
  ticking = true;
  try {
    await refreshJobs();
    if (!state.selected && state.jobs.length) select(state.jobs[0].id);
    else if (state.selected && !state.selected.startsWith("up-")) await loadDetail();
  } finally {
    ticking = false;
  }
}

init().catch((err) => {
  document.body.innerHTML = `<p style="padding:24px">Could not start: ${escapeHtml(String(err.message || err))}</p>`;
});

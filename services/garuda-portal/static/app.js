// garuda-portal SPA: EventSource + fetch only, no framework. Keep under ~200 lines.

const PANELS = ["overview", "camera", "depth", "range", "inference", "followsim", "calibration", "readiness", "settings", "system"];
let lastLoaded = {}; // Track loaded config for dirty detection
let lastReadinessReport = null; // Track last readiness report for download

function showPanel(name) {
  for (const p of PANELS) {
    const el = document.getElementById(`panel-${p}`);
    if (el) el.classList.toggle("hidden", p !== name);
  }
  // Tracking gets the full viewport width (cockpit layout, 2026-08-09 spec);
  // every other tab keeps the 960px reading column.
  const mainEl = document.querySelector("main");
  if (mainEl) mainEl.classList.toggle("wide", name === "inference");
  for (const a of document.querySelectorAll("nav a")) {
    a.classList.toggle("active", a.dataset.panel === name);
  }
  if (name === "system") refreshLogs();
  if (name === "settings") loadConfig();
  if (name === "readiness") runReadinessChecks();
  // Camera tab: the portal's own CameraStream (rpicam-vid on the CM3) is now lazy
  // on the backend (CameraStream.acquire()/release(), see portal.py) — it only
  // grabs the camera while a client is actually connected to /api/stream. Setting
  // the <img> src only while this tab is open (and clearing it on leave) is what
  // makes a connected client actually mean "someone is looking", instead of the
  // old static src="/api/stream" which opened the connection the instant the page
  // loaded, camera tab or not — that's the boot-time CM3 grab that starved
  // garuda-mono (twice), just deferred from service-start to page-load.
  const camImg = document.getElementById("camera-stream");
  if (camImg) {
    if (name === "camera") {
      if (!camImg.getAttribute("src")) camImg.src = "/api/stream?t=" + Date.now();
    } else if (camImg.getAttribute("src")) {
      camImg.removeAttribute("src");
    }
  }
  // Depth tab embeds the garuda-depth service (:8097). Load its iframe only while the
  // tab is open (blank otherwise) so its stream — and thus its compute — pauses on exit.
  const df = document.getElementById("depth-frame");
  if (df) {
    const depthUrl = `${location.protocol}//${location.hostname}:8097/`;
    if (name === "depth") { if (df.src !== depthUrl) df.src = depthUrl; }
    else if (df.src && df.src !== "about:blank") df.src = "about:blank";
  }
  // Target Range tab: embed garuda-depth's /range_stream (stereo-left + boxes + Z) only while
  // open; on leave, clear the lock so garuda-depth idles the detector + SGBM (no leaked compute).
  const rv = document.getElementById("range-view");
  if (rv) {
    const rbase = `${location.protocol}//${location.hostname}:8097`;
    if (name === "range") {
      rv.src = `${rbase}/range_stream?t=` + Date.now();
      startRangePoll();
    } else {
      if (rv.getAttribute("src")) {
        fetch(`${rbase}/api/range_target?class=`, { method: "POST" }).catch(() => { /* depth down */ });
        rv.removeAttribute("src");
      }
      stopRangePoll();
    }
  }
  // Tracking tab (the converged follow view): garuda-mono (:8098) is the sole CM3
  // holder now, so this loads mono's own annotated stream directly — lazily, only
  // while the panel is open (same load-on-enter/unload-on-leave pattern the old
  // mono/depth/range tabs use), instead of waiting on the portal's own
  // InferenceManager start/stop (that child path is unused by tracking now).
  const trkImg = document.getElementById("inference-stream");
  if (trkImg) {
    const mbase = `${location.protocol}//${location.hostname}:8098`;
    const stage = document.getElementById("follow-stage");
    const trackBanner = document.getElementById("tracking-banner");
    const infHint = document.getElementById("inference-hint");
    const planCol = document.getElementById("plan-col");
    const depthCol = document.getElementById("track-depth-col");
    if (name === "inference") {
      if (!trkImg.getAttribute("src")) trkImg.src = `${mbase}/stream?t=` + Date.now();
      if (stage) stage.classList.remove("hidden");
      if (trackBanner) trackBanner.classList.remove("hidden");
      if (infHint) infHint.classList.remove("hidden");
      if (planCol) planCol.classList.remove("hidden");
      if (depthCol) depthCol.classList.remove("hidden");
    } else {
      if (trkImg.getAttribute("src")) trkImg.removeAttribute("src");
      if (stage) stage.classList.add("hidden");
      if (trackBanner) trackBanner.classList.add("hidden");
      if (infHint) infHint.classList.add("hidden");
      if (planCol) planCol.classList.add("hidden");
      if (depthCol) depthCol.classList.add("hidden");
      setDepthEngineOff();
      _npuFrames = [];
      setStat("idle", "tile-npu-fps", "inf-npu-fps");
      setStat("idle", "tile-pcie-rate", "inf-pcie-rate");
    }
  }
}

function wireNav() {
  for (const a of document.querySelectorAll("nav a")) {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      showPanel(a.dataset.panel);
    });
  }
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function onHealth(data) {
  setText("tile-throttled", data.throttled ?? "unknown");
  setStat(data.temp_c != null ? `${data.temp_c} C` : "unknown", "tile-temp", "inf-temp");
  setText("tile-npu", data.npu ?? "unknown");
  setText("tile-disk", data.disk_free_gb != null ? `${data.disk_free_gb} GB` : "unknown");
  setText("tile-uptime", data.uptime_s != null ? `${Math.round(data.uptime_s)} s` : "unknown");
  setStat(data.cpu_pct != null ? `${data.cpu_pct}%` : "unknown", "tile-cpu", "inf-cpu");
  setText("tile-load", Array.isArray(data.loadavg) ? data.loadavg[0].toFixed(2) : "unknown");
  renderCpuCores(data.cpu_cores);
  updateMonoPowerBtn(data.services);
  if (data.pcie && data.pcie.gen) {
    setStat(`Gen${data.pcie.gen} x${data.pcie.width} · ${data.pcie.gbps} GB/s`, "tile-pcie", "inf-pcie");
  }
  const list = document.getElementById("services-list");
  if (list && data.services) {
    list.innerHTML = "";
    for (const [unit, up] of Object.entries(data.services)) {
      const li = document.createElement("li");
      li.textContent = `${unit}: ${up ? "up" : "down"}`;
      li.className = up ? "ok" : "bad";
      list.appendChild(li);
    }
  }
  // Every health tick carries camera_mode too, so a page reload (or the first
  // tick after connect) syncs the camera banner/button without waiting for
  // the next start/stop transition event.
  if (data.camera_mode) applyCameraMode(data.camera_mode);
  // Update GARUDA_SIM display in settings panel
  const simDisplay = document.getElementById("sim-display");
  if (simDisplay) {
    simDisplay.value = data.garuda_sim != null ? data.garuda_sim : "sim gate: not reported";
  }
}

// --- Utilisation: per-core CPU bars + NPU inference rate ---------------------
function renderCpuCores(cores) {
  // Updates every .cpu-cores container (Overview tile + Inference-page stats).
  for (const box of document.querySelectorAll(".cpu-cores")) {
    box.innerHTML = "";
    if (!Array.isArray(cores)) continue;
    for (const c of cores) {
      const bar = document.createElement("div");
      bar.className = "cpu-core";
      bar.title = `core: ${c}%`;
      const fill = document.createElement("div");
      fill.className = "cpu-core-fill";
      fill.style.width = Math.max(0, Math.min(100, c)) + "%";
      bar.appendChild(fill);
      box.appendChild(bar);
    }
  }
}

// Set the same value on the Overview tile and the Inference-page stat (both guarded).
function setStat(value, ...ids) { for (const id of ids) setText(id, value); }

// NPU throughput: detections arrive one per inferred frame, so their rate is the
// live NPU inference fps (an honest throughput reading; a true compute busy-% /
// TOPS isn't exposed by the GStreamer/hailonet path — see the Overview note).
// Also estimates PCIe input bandwidth: each frame is DMA'd host->NPU over the
// link, so ~ frame_bytes * fps (an estimate; the runtime has no transfer counter).
let _npuFrames = [];
function tickNpu(frame) {
  const now = performance.now();
  _npuFrames.push(now);
  const cutoff = now - 2000;
  while (_npuFrames.length && _npuFrames[0] < cutoff) _npuFrames.shift();
  if (_npuFrames.length < 2) return;
  const span = (_npuFrames[_npuFrames.length - 1] - _npuFrames[0]) / 1000;
  const fps = span > 0 ? (_npuFrames.length - 1) / span : 0;
  setStat(`${fps.toFixed(0)} fps`, "tile-npu-fps", "inf-npu-fps");
  const pf = (frame && frame.pixel_format) || "";
  const bpp = /^(RGB|BGR)/.test(pf) ? 3 : (/^(YUV|NV)/.test(pf) ? 1.5 : 2);
  const bytes = frame && frame.width && frame.height ? frame.width * frame.height * bpp : 0;
  if (bytes) setStat(`~${(bytes * fps / 1e6).toFixed(0)} MB/s`, "tile-pcie-rate", "inf-pcie-rate");
}

function onDetections(data) {
  tickNpu(data.frame);
  const list = document.getElementById("detections-list");
  if (!list) return;
  list.innerHTML = "";
  const dets = data.detections || [];
  if (dets.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "no detections this frame";
    list.appendChild(li);
    return;
  }
  for (const d of dets) {
    const li = document.createElement("li");
    const pct = Math.round((d.confidence || 0) * 100);
    li.textContent = `${d.label} (${pct}%)`;
    list.appendChild(li);
  }
}

// --- Follow (Task 7): track IDs + FollowCommand readout, pin-to-follow ----
let lastPinnedId = null; // client-side echo of the last id we asked to pin

function onFollow(data) {
  renderTracks(data.tracks || []);
  renderFollowCommand(data.command || {});
  updateFollowOverlay(data);
  updateTrackingBanner(data);
  if (window.FollowViz) window.FollowViz.onLiveFollow(data);
}

// Clear, prominent "which subject is being tracked" banner above the feed.
function updateTrackingBanner(data) {
  const banner = document.getElementById("tracking-banner");
  const text = document.getElementById("tracking-text");
  if (!banner || !text) return;
  const cmd = data.command || {};
  banner.classList.remove("tracking", "hold"); // keep .hidden (managed by applyCameraMode)
  if (cmd.valid && cmd.target_id != null) {
    const t = (data.tracks || []).find((tr) => tr.id === cmd.target_id);
    const label = t ? t.label : "target";
    const pct = t ? ` · ${Math.round((t.confidence || 0) * 100)}%` : "";
    text.textContent = `Tracking ${label} #${cmd.target_id}${pct}`;
    banner.classList.add("tracking");
  } else {
    text.textContent = cmd.reason ? `No lock — ${cmd.reason}` : "No target — searching";
    banner.classList.add("hold");
  }
}

function renderTracks(tracks) {
  // Reuses the same "Live detections" list as onDetections, but sourced from
  // the follow event's tracks (id + label + confidence + a pin button) so
  // each row can read "person #3 (47%)" instead of just "person (47%)".
  const list = document.getElementById("detections-list");
  if (!list) return;
  list.innerHTML = "";
  if (tracks.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "no detections this frame";
    list.appendChild(li);
    return;
  }
  for (const t of tracks) {
    const li = document.createElement("li");
    const pct = Math.round((t.confidence || 0) * 100);
    const label = document.createElement("span");
    label.textContent = `${t.label} #${t.id} (${pct}%)`;
    li.appendChild(label);
    const btn = document.createElement("button");
    btn.className = "follow-pin-btn";
    const following = t.id === lastPinnedId;
    btn.textContent = following ? "Following" : `Follow #${t.id}`;
    btn.disabled = following;
    btn.addEventListener("click", () => pinTrack(t.id));
    li.appendChild(btn);
    list.appendChild(li);
  }
}

function renderFollowCommand(cmd) {
  const validEl = document.getElementById("follow-valid");
  if (validEl) {
    validEl.textContent = cmd.valid ? "TRACKING" : "HOLD";
    validEl.className = `follow-badge ${cmd.valid ? "ok" : "bad"}`;
  }
  setText("follow-reason", cmd.reason || "");
  setText("follow-target", cmd.target_id != null ? `#${cmd.target_id}` : "none");
  setText("follow-yaw", (cmd.yaw_rate ?? 0).toFixed(2));
  setText("follow-forward", (cmd.forward_vel ?? 0).toFixed(2));
  setText("follow-vertical", (cmd.vertical_vel ?? 0).toFixed(2));
}

async function pinTrack(trackId) {
  lastPinnedId = trackId;
  await postAction("/api/actions/follow", { track_id: trackId });
  setText("follow-pinned-status", `pinned #${trackId}`);
}

async function unpinTrack() {
  lastPinnedId = null;
  await postAction("/api/actions/follow", { track_id: null });
  setText("follow-pinned-status", "auto");
}

// --- Follow overlay: click-on-video to lock a target, reticle + command HUD --
// Draws over the annotated /api/stream feed using the same `follow` SSE data
// (tracks[].bbox normalized [x,y,w,h]; command from FollowCommand). The server
// already chose the target (command.target_id) via the real select()/compute();
// the overlay only visualises it and turns a click into a pin. Presentation
// only — no control decisions happen here.
const FOLLOW_COL = {
  locked: "#ff5d73", auto: "#ffb547", person: "#7fd1b9", other: "#9d8bff",
  ok: "#5ed582", bad: "#e0876b", dim: "#9fb0bf", reticle: "rgba(127,209,185,0.55)",
};
const FOLLOW_DEADZONE = 0.03; // mirrors FollowConfig.deadzone (display only)
// COCO-17 skeleton bones (pose mode only; kpts absent in detect mode -> no-op).
const BONES = [[5, 7], [7, 9], [6, 8], [8, 10], [5, 6], [11, 12], [5, 11], [6, 12],
               [11, 13], [13, 15], [12, 14], [14, 16], [0, 5], [0, 6]];
const KPT_CONF_MIN = 0.35;
let followTracks = [];
let followCmd = {};
let _trkFrame = { width: 1280, height: 720 }; // last known mono frame size, for click->pixel mapping

function updateFollowOverlay(data) {
  followTracks = data.tracks || [];
  followCmd = data.command || {};
  drawFollowOverlay();
}

function pointInBbox(bbox, nx, ny) {
  // bbox normalized [x, y, w, h], top-left origin
  return nx >= bbox[0] && nx <= bbox[0] + bbox[2] &&
         ny >= bbox[1] && ny <= bbox[1] + bbox[3];
}

function hitTestTracks(tracks, nx, ny) {
  // Topmost (last-drawn wins) track whose bbox contains the point, else null.
  for (let i = tracks.length - 1; i >= 0; i--) {
    const b = tracks[i].bbox;
    if (Array.isArray(b) && b.length === 4 && pointInBbox(b, nx, ny)) return tracks[i].id;
  }
  return null;
}

function followOverlayGeom() {
  const img = document.getElementById("inference-stream");
  const cv = document.getElementById("follow-overlay");
  if (!img || !cv) return null;
  const w = img.clientWidth, h = img.clientHeight;
  if (w === 0 || h === 0) return null; // no frame sized yet
  return { cv, w, h };
}

function drawFollowOverlay() {
  const g = followOverlayGeom();
  if (!g) return;
  const { cv, w, h } = g;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const px = (v) => v * w, py = (v) => v * h;
  const targetId = followCmd.target_id;
  const valid = !!followCmd.valid;

  // aim: centre reticle + deadzone band (the drone yaws to bring the target here)
  ctx.fillStyle = "rgba(127,209,185,0.08)";
  ctx.fillRect(px(0.5 - FOLLOW_DEADZONE), 0, w * 2 * FOLLOW_DEADZONE, h);
  ctx.strokeStyle = FOLLOW_COL.reticle;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(px(0.5), 0);
  ctx.lineTo(px(0.5), h);
  ctx.stroke();
  ctx.setLineDash([]);

  // track boxes (non-target subtle; target bold with corner brackets)
  ctx.font = "12px -apple-system, 'Segoe UI', sans-serif";
  for (const t of followTracks) {
    const b = t.bbox;
    if (!Array.isArray(b) || b.length !== 4) continue;
    const bx = px(b[0]), by = py(b[1]), bw = w * b[2], bh = h * b[3];
    const isTarget = targetId != null && t.id === targetId;
    // Once a target is locked, hide the other detection boxes to reduce clutter;
    // with no lock, keep them all visible so any can be clicked. (Track list stays
    // as the way to switch to a hidden subject.)
    if (targetId != null && valid && !isTarget) continue;
    const locked = isTarget && t.id === lastPinnedId;
    let col = t.label === "person" ? FOLLOW_COL.person : FOLLOW_COL.other;
    if (isTarget) col = locked ? FOLLOW_COL.locked : FOLLOW_COL.auto;
    ctx.strokeStyle = col;
    ctx.globalAlpha = isTarget ? 1 : 0.5;
    ctx.lineWidth = isTarget ? 2.4 : 1.2;
    ctx.strokeRect(bx, by, bw, bh);
    if (isTarget) {
      const L = Math.min(bw, bh) * 0.28;
      ctx.lineWidth = 3;
      const corners = [[bx, by, 1, 1], [bx + bw, by, -1, 1], [bx, by + bh, 1, -1], [bx + bw, by + bh, -1, -1]];
      for (const [cx, cy, sx, sy] of corners) {
        ctx.beginPath();
        ctx.moveTo(cx + sx * L, cy);
        ctx.lineTo(cx, cy);
        ctx.lineTo(cx, cy + sy * L);
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    ctx.fillStyle = col;
    const pct = Math.round((t.confidence || 0) * 100);
    ctx.fillText(`${t.label} #${t.id} ${pct}%${locked ? "  ◎ LOCK" : ""}`, bx, Math.max(12, by - 4));
  }

  // pose skeleton overlay (Task 4): mono's own tracks (window._monoTracks, set in
  // onMono) carry kpts in SOURCE-FRAME PIXELS when pose mode is active; normalize
  // by _trkFrame before applying the same px/py transform the boxes use above.
  const fw = _trkFrame.width || 1, fh = _trkFrame.height || 1;
  for (const mt of (window._monoTracks || [])) {
    const kpts = mt.kpts;
    if (!Array.isArray(kpts)) continue;
    ctx.strokeStyle = FOLLOW_COL.reticle;
    ctx.lineWidth = 2;
    for (const [a, b] of BONES) {
      const ka = kpts[a], kb = kpts[b];
      if (!ka || !kb || ka[2] < KPT_CONF_MIN || kb[2] < KPT_CONF_MIN) continue;
      ctx.beginPath();
      ctx.moveTo(px(ka[0] / fw), py(ka[1] / fh));
      ctx.lineTo(px(kb[0] / fw), py(kb[1] / fh));
      ctx.stroke();
    }
    ctx.fillStyle = FOLLOW_COL.locked;
    for (const k of kpts) {
      if (!k || k[2] < KPT_CONF_MIN) continue;
      ctx.beginPath();
      ctx.arc(px(k[0] / fw), py(k[1] / fh), 3, 0, Math.PI * 2);
      ctx.fill();
    }
    // on-video gesture label (owner request 2026-08-10): draw the active gesture's text
    // directly above the gesturing person's box — the sidebar chip alone was too easy to miss
    const g = window._monoGesture;
    if (g && g.name && g.track_id === mt.id) {
      ctx.save();
      ctx.font = "bold 24px system-ui, sans-serif";
      ctx.lineWidth = 5;
      ctx.strokeStyle = "rgba(0,0,0,.8)";
      ctx.fillStyle = "#5ed582";
      const label = (typeof GESTURE_LABELS !== "undefined" && GESTURE_LABELS[g.name]) || g.name;
      const gx = px(mt.x / fw), gy = Math.max(28, py(mt.y / fh) - 14);
      ctx.strokeText(label, gx, gy);
      ctx.fillText(label, gx, gy);
      ctx.restore();
    }
  }

  // command HUD (bottom-left): tracking/hold + what the drone would do
  const y0 = h - 10;
  ctx.fillStyle = valid ? FOLLOW_COL.ok : FOLLOW_COL.bad;
  ctx.fillText(valid ? `TRACKING #${targetId}` : `HOLD · ${followCmd.reason || ""}`, 10, y0);
  if (valid) {
    const yaw = followCmd.yaw_rate || 0, fwd = followCmd.forward_vel || 0;
    const turn = yaw > 0.02 ? `turn ▶ ${yaw.toFixed(2)}` : (yaw < -0.02 ? `◀ turn ${Math.abs(yaw).toFixed(2)}` : "centred");
    const move = fwd > 0.02 ? `close ▲ ${fwd.toFixed(2)}` : (fwd < -0.02 ? `back ▼ ${Math.abs(fwd).toFixed(2)}` : "at range");
    ctx.fillStyle = FOLLOW_COL.dim;
    ctx.fillText(`${turn}    ${move}`, 160, y0);
  }
}

function onFollowOverlayClick(ev) {
  const cv = document.getElementById("follow-overlay");
  if (!cv) return;
  const r = cv.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return;
  const nx = (ev.clientX - r.left) / r.width;
  const ny = (ev.clientY - r.top) / r.height;
  const id = hitTestTracks(followTracks, nx, ny);
  if (id != null) pinTrack(id); // empty-space click: keep current target
  // Forward the same click to mono — it does its own server-side hit test/lock
  // against the CM3 frame it owns (pixel coords, from the last known frame size).
  const mbase = `${location.protocol}//${location.hostname}:8098`;
  fetch(`${mbase}/api/mono_click?x=${Math.round(nx * _trkFrame.width)}&y=${Math.round(ny * _trkFrame.height)}`,
        { method: "POST" }).catch(() => {});
}

// InferenceManager's CM3 child path is no longer what drives the Tracking tab (mono
// is the sole CM3 holder — see showPanel()'s "inference" block); this now only covers
// the Camera tab's own annotated-feed banner/capture-disable while that child runs.
function applyCameraMode(mode) {
  const inInference = mode === "inference";
  const banner = document.getElementById("camera-banner");
  if (banner) banner.classList.toggle("hidden", !inInference);
  const captureBtn = document.getElementById("btn-capture-still");
  if (captureBtn) captureBtn.disabled = inInference;
}

function onCameraMode(data) {
  applyCameraMode(data.mode);
}

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (msg.kind === "health") onHealth(msg.data);
    else if (msg.kind === "detections") onDetections(msg.data);
    else if (msg.kind === "camera_mode") onCameraMode(msg.data);
    else if (msg.kind === "follow") onFollow(msg.data);
    else if (msg.kind === "mono") onMono(msg.data);
    else if (msg.kind === "error") onBusError(msg.data);
  };
}

async function postAction(path, body) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return resp.json();
}

async function captureStill() {
  setText("capture-status", "capturing…");
  const result = await postAction("/api/actions/capture-still", {});
  setText("capture-status", result.ok ? `saved ${result.file}` : "no frame available");
}

async function refreshLogs() {
  const pre = document.getElementById("system-logs");
  if (!pre) return;
  try {
    pre.textContent = await (await fetch("/api/logs")).text();
  } catch {
    pre.textContent = "logs unavailable";
  }
}

async function loadConfig() {
  const form = document.getElementById("settings-form");
  const status = document.getElementById("settings-status");
  if (!form || !status) return;
  try {
    const resp = await fetch("/api/config");
    const data = await resp.json();
    if (!data.ok) {
      setText("settings-status", `Error loading: ${data.detail || "unknown"}`);
      return;
    }
    const config = data.result || {};
    lastLoaded = {}; // Reset dirty tracking
    // Populate form fields from config and record in lastLoaded
    for (const field of form.elements) {
      if (!field.name || field.name === "ptp.enabled") continue;
      if (field.name in config) {
        field.value = config[field.name];
        lastLoaded[field.name] = config[field.name];
      }
    }
    // Handle checkbox specially
    const ptpCheckbox = document.getElementById("ptp-enabled");
    if (ptpCheckbox && "ptp.enabled" in config) {
      ptpCheckbox.checked = config["ptp.enabled"];
      lastLoaded["ptp.enabled"] = config["ptp.enabled"];
    }
    setText("settings-status", "Loaded");
  } catch (e) {
    setText("settings-status", `Error: ${e.message}`);
  }
}

async function saveConfig() {
  const form = document.getElementById("settings-form");
  const status = document.getElementById("settings-status");
  if (!form || !status) return;
  setText("settings-status", "Saving…");

  try {
    // Ensure a load has happened
    if (Object.keys(lastLoaded).length === 0) {
      await loadConfig();
      if (Object.keys(lastLoaded).length === 0) {
        setText("settings-status", "Could not load config");
        return;
      }
    }

    const saved = [];
    const rejected = [];

    // Collect changed fields and attempt to save all
    for (const field of form.elements) {
      if (!field.name) continue;

      // Coerce value by input type
      let value;
      if (field.type === "checkbox") {
        value = field.checked;
      } else if (field.type === "number") {
        const parsed = parseInt(field.value, 10);
        if (isNaN(parsed)) continue; // Skip NaN
        value = parsed;
      } else {
        value = field.value;
        if (!value) continue; // Skip empty text fields
      }

      // Check if value changed from lastLoaded
      if (field.name in lastLoaded && lastLoaded[field.name] === value) {
        continue; // No change, skip
      }

      // Attempt to save this key
      try {
        const req = {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: field.name, value: value })
        };
        const resp = await fetch("/api/config", req);
        const result = await resp.json();
        if (result.ok) {
          saved.push(field.name);
          lastLoaded[field.name] = value; // Update lastLoaded on success
        } else {
          rejected.push(`${field.name} (${result.detail || "unknown"})`);
        }
      } catch (e) {
        rejected.push(`${field.name} (${e.message})`);
      }
    }

    // Compose combined status message
    let msg = "";
    if (saved.length > 0) msg += `saved: ${saved.join(", ")}`;
    if (rejected.length > 0) {
      if (msg) msg += "; ";
      msg += `rejected: ${rejected.join(", ")}`;
    }
    setText("settings-status", msg || "No changes");
  } catch (e) {
    setText("settings-status", `Error: ${e.message}`);
  }
}

async function runReadinessChecks() {
  try {
    const resp = await fetch("/api/readiness/report");
    const report = await resp.json();
    lastReadinessReport = report;
    renderReadinessReport(report);
  } catch (e) {
    const banner = document.getElementById("readiness-banner");
    if (banner) {
      banner.textContent = `Error: ${e.message}`;
      banner.className = "readiness-banner no-go";
    }
  }
}

function renderReadinessReport(report) {
  const banner = document.getElementById("readiness-banner");
  const checksDiv = document.getElementById("readiness-checks");
  if (!banner || !checksDiv) return;

  // Set banner
  banner.textContent = report.overall.toUpperCase();
  banner.className = `readiness-banner ${report.overall}`;

  // Render checks
  checksDiv.innerHTML = "";
  for (const check of report.checks) {
    const div = document.createElement("div");
    div.className = "readiness-check";

    const statusBadge = document.createElement("div");
    statusBadge.className = `readiness-check-status ${check.status}`;
    statusBadge.textContent = check.status.toUpperCase();

    const details = document.createElement("div");
    details.className = "readiness-check-details";

    const id = document.createElement("div");
    id.className = "readiness-check-id";
    id.textContent = check.id;

    const evidence = document.createElement("div");
    evidence.className = "readiness-check-evidence";
    evidence.textContent = check.evidence;

    details.appendChild(id);
    details.appendChild(evidence);
    div.appendChild(statusBadge);
    div.appendChild(details);
    checksDiv.appendChild(div);
  }
}

function downloadReadinessReport() {
  if (!lastReadinessReport) {
    alert("No report to download. Run checks first.");
    return;
  }
  const now = new Date().toISOString().split("T")[0];
  const filename = `readiness-${now}.json`;
  const blob = new Blob([JSON.stringify(lastReadinessReport, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// --- Calibration wizard (Step 5): pattern form -> capture -> solve ---------
// V1 is manual-trigger only (no auto-capture) — that's the honest scope, not
// a placeholder. per_zone mirrors CalibSession's CoverageGrid default; it
// isn't exposed as a tuning knob in V1, only pattern/square size are.
const CALIB_PER_ZONE = 3;
let calibCaptureCount = 0;

function renderCalibGrid(zoneCounts) {
  const grid = document.getElementById("calib-grid");
  if (!grid) return;
  grid.innerHTML = "";
  const counts = zoneCounts || [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const row of counts) {
    for (const n of row) {
      const cell = document.createElement("div");
      cell.className = "calib-zone";
      const ratio = Math.min(n / CALIB_PER_ZONE, 1);
      cell.style.backgroundColor = `rgba(127, 209, 185, ${0.12 + 0.7 * ratio})`;
      cell.textContent = String(n);
      grid.appendChild(cell);
    }
  }
}

async function startCalibSession() {
  const cols = parseInt(document.getElementById("calib-cols")?.value, 10) || 9;
  const rows = parseInt(document.getElementById("calib-rows")?.value, 10) || 6;
  const squareMm = parseFloat(document.getElementById("calib-square-mm")?.value) || 25;
  calibCaptureCount = 0;
  const result = await postAction("/api/actions/calib-start",
    { pattern: [cols, rows], square_size_mm: squareMm });
  renderCalibGrid(result.zone_counts);
  setText("calib-session-status", "session started");
  const captureBtn = document.getElementById("btn-calib-capture");
  if (captureBtn) captureBtn.disabled = false;
  const solveBtn = document.getElementById("btn-calib-solve");
  if (solveBtn) solveBtn.disabled = true;
  setText("calib-verdict", "");
  const verdict = document.getElementById("calib-verdict");
  if (verdict) verdict.className = "calib-verdict";
}

async function captureCalib() {
  const result = await postAction("/api/actions/calib-capture", {});
  if (!result.ok) {
    setText("calib-capture-status", result.detail || "capture failed");
    return;
  }
  if (result.added) calibCaptureCount++;
  renderCalibGrid(result.zone_counts);
  setText("calib-capture-status",
    result.corners_found ? `corners found (${calibCaptureCount} captured)` : "no board detected in frame");
  const solveBtn = document.getElementById("btn-calib-solve");
  if (solveBtn) solveBtn.disabled = !(result.complete || calibCaptureCount >= 9);
}

async function solveCalib() {
  const result = await postAction("/api/actions/calib-solve", {});
  const verdict = document.getElementById("calib-verdict");
  if (!verdict) return;
  if (!result.ok) {
    verdict.textContent = result.detail || "solve failed";
    verdict.className = "calib-verdict fail";
    return;
  }
  let cls, label;
  if (result.rms < 0.5) { cls = "pass"; label = "PASS"; }
  else if (result.rms < 1.0) { cls = "marginal"; label = "MARGINAL"; }
  else { cls = "fail"; label = "FAIL"; }
  verdict.textContent = `${label} (rms ${result.rms.toFixed(3)} px)`;
  verdict.className = `calib-verdict ${cls}`;
}

function wireActions() {
  document.getElementById("btn-capture-still")?.addEventListener("click", captureStill);
  document.getElementById("btn-follow-auto")?.addEventListener("click", unpinTrack);
  document.getElementById("btn-refresh-logs")?.addEventListener("click", refreshLogs);
  document.getElementById("btn-settings-load")?.addEventListener("click", loadConfig);
  document.getElementById("btn-settings-save")?.addEventListener("click", saveConfig);
  document.getElementById("btn-readiness-run")?.addEventListener("click", runReadinessChecks);
  document.getElementById("readiness-download")?.addEventListener("click", (e) => {
    e.preventDefault();
    downloadReadinessReport();
  });
  document.getElementById("btn-calib-start")?.addEventListener("click", startCalibSession);
  document.getElementById("btn-calib-capture")?.addEventListener("click", captureCalib);
  document.getElementById("btn-calib-solve")?.addEventListener("click", solveCalib);
}

function wireFollowOverlay() {
  document.getElementById("follow-overlay")?.addEventListener("click", onFollowOverlayClick);
  // MJPEG multipart fires 'load' when the first frame arrives — size the overlay then.
  document.getElementById("inference-stream")?.addEventListener("load", drawFollowOverlay);
  window.addEventListener("resize", drawFollowOverlay);
}

// Force the depth panel to its OFF state: drop the <img> src so garuda-depth loses its
// only viewer and stops computing (SGBM ~2 cores freed), reveal the placeholder, and light
// the "Off" button. Called on the Off button and whenever we leave inference.
function setDepthEngineOff() {
  const depthImg = document.getElementById("track-depth");
  const offNote = document.getElementById("depth-off-note");
  const status = document.getElementById("depth-eng-status");
  if (depthImg) { depthImg.removeAttribute("src"); depthImg.classList.add("hidden"); }
  if (offNote) offNote.classList.remove("hidden");
  document.querySelectorAll(".deng").forEach((x) => x.classList.toggle("active", x.dataset.eng === "off"));
  if (status) status.textContent = "Depth off — tracking stays light. Pick an engine to start.";
}

// Depth-engine swap (tracking-view panel). POSTs to the garuda-depth server (:8097). "Off"
// stops computing entirely (default). CPU engines (sgbm/pointcloud) switch instantly; NPU
// engines re-exec the Hailo (~9 s), offloading the CPU but then sharing the NPU with the
// CM3 detection. garuda-depth only burns CPU while the <img> below is actually loading.
function wireDepthEngines() {
  const status = document.getElementById("depth-eng-status");
  const depthImg = document.getElementById("track-depth");
  const offNote = document.getElementById("depth-off-note");
  const base = () => `${location.protocol}//${location.hostname}:8097`;
  document.querySelectorAll(".deng").forEach((b) => b.addEventListener("click", async () => {
    const eng = b.dataset.eng;
    if (eng === "off") { setDepthEngineOff(); return; }
    const npu = eng !== "sgbm" && eng !== "pointcloud";
    document.querySelectorAll(".deng").forEach((x) => x.classList.toggle("active", x === b));
    if (offNote) offNote.classList.add("hidden");
    if (depthImg) depthImg.classList.remove("hidden");
    if (status) status.textContent = npu
      ? `switching to ${eng} on the NPU — Hailo reset, ~9 s…`
      : `switching to ${eng} (CPU)…`;
    try { await fetch(`${base()}/api/model?name=${eng}`, { method: "POST" }); } catch (e) { /* depth svc down */ }
    setTimeout(() => {
      if (depthImg) depthImg.src = `${base()}/stream?t=` + Date.now();
      if (status) status.textContent = `engine: ${eng}` +
        (npu ? " — NPU (CPU offloaded; shares the Hailo with detection)" : " — CPU (~2 cores, metric stereo)");
    }, npu ? 9500 : 800);
  }));
}

// --- Target Range (garuda-depth :8097, Option B): click-to-lock + badged metric distance ---
let _rangePollTimer = null;
let _rangeLastValid = null;                 // {z, t} — hold briefly through detection flicker

function _rangeBase() { return `${location.protocol}//${location.hostname}:8097`; }

function startRangePoll() {
  if (_rangePollTimer) return;
  _rangePollTimer = setInterval(async () => {
    let d = null;
    try { d = await (await fetch(`${_rangeBase()}/api/target_range`)).json(); } catch (e) { /* down */ }
    const distEl = document.getElementById("range-dist");
    const badgeEl = document.getElementById("range-badge");
    if (!distEl || !badgeEl) return;
    if (d && d.valid && typeof d.z_m === "number") {
      _rangeLastValid = { z: d.z_m, t: Date.now() };
      distEl.textContent = d.z_m.toFixed(2) + " m";
      badgeEl.textContent = d.count > 1 ? `stereo · 1/${d.count}` : "stereo";
      badgeEl.className = "dist-badge stereo";
    } else if (_rangeLastValid && Date.now() - _rangeLastValid.t < 1500) {
      distEl.textContent = _rangeLastValid.z.toFixed(2) + " m";   // hold through grayscale flicker
      badgeEl.textContent = "stereo"; badgeEl.className = "dist-badge hold";
    } else if (d && d.class) {
      distEl.textContent = "—"; badgeEl.textContent = "searching"; badgeEl.className = "dist-badge searching";
    } else {
      distEl.textContent = "—"; badgeEl.textContent = ""; badgeEl.className = "dist-badge";
    }
  }, 300);
}

function stopRangePoll() {
  if (_rangePollTimer) { clearInterval(_rangePollTimer); _rangePollTimer = null; }
  _rangeLastValid = null;
}

function wireRange() {
  const view = document.getElementById("range-view");
  const sel = document.getElementById("range-class");
  const clr = document.getElementById("range-clear");
  if (sel) sel.addEventListener("change", () => {
    _rangeLastValid = null;                                  // drop the held value: target changed
    fetch(`${_rangeBase()}/api/range_target?class=${encodeURIComponent(sel.value)}`, { method: "POST" }).catch(() => {});
  });
  if (clr) clr.addEventListener("click", () => {
    _rangeLastValid = null;
    if (sel) sel.value = "";
    fetch(`${_rangeBase()}/api/range_target?class=`, { method: "POST" }).catch(() => {});
  });
  if (view) view.addEventListener("click", (e) => {
    if (!view.naturalWidth) return;                          // stream not loaded yet
    _rangeLastValid = null;                                  // new target: don't hold the old distance
    const r = view.getBoundingClientRect();
    const nx = (e.clientX - r.left) / r.width * view.naturalWidth;    // -> native left-image px
    const ny = (e.clientY - r.top) / r.height * view.naturalHeight;
    fetch(`${_rangeBase()}/api/range_click?x=${nx.toFixed(0)}&y=${ny.toFixed(0)}`, { method: "POST" })
      .then((res) => res.json()).then((j) => {
        if (j.class && sel) {
          if (![...sel.options].some((o) => o.value === j.class)) sel.add(new Option(j.class, j.class));
          sel.value = j.class;                               // reflect the locked class (add if off-list)
        }
      }).catch(() => {});
  });
}

// --- Tracking HUD (garuda-mono :8098, via the portal's `mono` bus events) ---------
// Distance readout + 60 s sparkline + engine/class/prompt/cal controls for the
// converged Tracking tab. mono is a single CM3 process shared by all viewers, so
// the controls POST straight to it (CORS `*`) rather than proxying through the
// portal.
const _trkHist = [];

// Gesture chip labels (Task 4: mono snapshot v2 top-level `gesture`, pose mode
// only — see hosts/pi5/mono_gestures.py for the FSM). STOP/FOLLOW ME act
// server-side (services/garuda-portal/gesture_actions.py's GestureReactor);
// POINT_* is HUD-only here.
const GESTURE_LABELS = {
  stop: "✋ STOP",
  follow_me: "🙋 FOLLOW ME",
  point_left: "👉 POINT L",
  point_right: "👉 POINT R",
};

function updateGestureChip(gesture) {
  const chip = document.getElementById("trk-gesture");
  if (!chip) return;
  const name = gesture && gesture.name;
  const label = name && GESTURE_LABELS[name];
  if (label) {
    chip.textContent = label;
    chip.classList.remove("hidden");
  } else {
    chip.classList.add("hidden");
  }
}

function onBusError(data) {
  if (!data || data.stage !== "mono") return;
  const banner = document.getElementById("mono-offline-banner");
  if (banner) banner.classList.remove("hidden");
}

function onMono(s) {
  // Any fresh snapshot means mono answered — clear the offline banner (covers
  // the stop/start recovery case; MonoClient only publishes `error` once per
  // outage, so there's no separate "back up" event to key off of).
  const offline = document.getElementById("mono-offline-banner");
  if (offline) offline.classList.add("hidden");
  if (s.frame && s.frame.width && s.frame.height) _trkFrame = s.frame;
  window._monoTracks = s.tracks || [];   // pose-mode kpts, consumed by drawFollowOverlay's skeleton pass
  window._monoGesture = s.gesture || null;   // consumed by the skeleton pass's on-video label
  updateGestureChip(s.gesture);
  const d = s.distance || {};
  setText("trk-dist", d.valid && d.est_m != null ? d.est_m.toFixed(2) + " m"
                                                 : (s.lock && s.lock.id != null ? "locked…" : "—"));
  _trkHist.push({ t: Date.now(), v: d.valid && d.est_m != null ? d.est_m : null });
  drawTrkChart();
  const eng = document.getElementById("trk-engine");
  if (eng && document.activeElement !== eng) eng.value = s.depth_engine || "y26d";
}

// 60 s distance sparkline — same algorithm as mono's own dev-GUI chart (garuda_mono.py
// PAGE), rendered into #trk-chart instead so the portal doesn't need mono's page at all.
function drawTrkChart() {
  const ch = document.getElementById("trk-chart");
  if (!ch) return;
  const g = ch.getContext("2d");
  const W = (ch.width = ch.clientWidth), H = ch.height;
  g.clearRect(0, 0, W, H);
  const now = Date.now(), span = 60000;
  while (_trkHist.length && now - _trkHist[0].t > span) _trkHist.shift();
  const vals = _trkHist.filter((p) => p.v != null).map((p) => p.v);
  if (!vals.length) {
    g.fillStyle = "#6b7a88";
    g.font = "12px sans-serif";
    g.fillText("distance history (60 s) — waiting for lock...", 8, 16);
    return;
  }
  let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  const pad = Math.max(0.05, (hi - lo) * 0.15);
  lo -= pad; hi += pad;
  g.strokeStyle = "#22303c";
  g.beginPath();
  for (let i = 1; i < 4; i++) { const y = (H * i) / 4; g.moveTo(0, y); g.lineTo(W, y); }
  g.stroke();
  g.strokeStyle = "#7fd1b9";
  g.lineWidth = 2;
  g.beginPath();
  let started = false;
  for (const p of _trkHist) {
    if (p.v == null) { started = false; continue; }
    const x = W - ((now - p.t) / span) * W, y = H - ((p.v - lo) / (hi - lo)) * H;
    if (!started) { g.moveTo(x, y); started = true; } else g.lineTo(x, y);
  }
  g.stroke();
  g.fillStyle = "#6b7a88";
  g.font = "11px sans-serif";
  g.fillText(hi.toFixed(2) + " m", 6, 12);
  g.fillText(lo.toFixed(2) + " m", 6, H - 4);
  const last = vals[vals.length - 1];
  g.fillStyle = "#7fd1b9";
  g.font = "bold 13px sans-serif";
  g.fillText(last.toFixed(2) + " m", W - 70, 14);
}

function wireTracking() {
  const mbase = `${location.protocol}//${location.hostname}:8098`;
  const on = (id, ev, fn) => { const el = document.getElementById(id); if (el) el.addEventListener(ev, fn); };
  on("trk-engine", "change", (e) =>
    fetch(`${mbase}/api/depth_engine?engine=${e.target.value}`, { method: "POST" }).catch(() => {}));
  on("trk-class", "change", (e) =>
    fetch(`${mbase}/api/mono_target?class=${encodeURIComponent(e.target.value)}`, { method: "POST" }).catch(() => {}));
  on("trk-prompt", "change", (e) => {
    const words = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
    fetch(`${mbase}/api/detector?mode=${words.length ? "world" : "primary"}` +
          words.map((w) => `&prompt=${encodeURIComponent(w)}`).join(""), { method: "POST" }).catch(() => {});
  });
  on("trk-cal-add", "click", () => {
    const v = parseFloat(document.getElementById("trk-cal-truth").value);
    if (v > 0) fetch(`${mbase}/api/mono_calibrate?dist=${v}`, { method: "POST" }).catch(() => {});
  });
  on("trk-cal-reset", "click", () =>
    fetch(`${mbase}/api/mono_calibrate_reset`, { method: "POST" }).catch(() => {}));
}

// Mono power toggle (2026-08-09 spec): state comes from the 2 s health events
// (services["garuda-mono"]); actions go through the portal -> configd whitelist
// path, NOT mono's own API (mono is down when we need "start").
let _monoPowerPending = false;
function updateMonoPowerBtn(services) {
  const btn = document.getElementById("trk-mono-power");
  if (!btn || !services || !("garuda-mono" in services) || _monoPowerPending) return;
  const up = !!services["garuda-mono"];
  btn.disabled = false;
  btn.dataset.active = String(up);
  btn.textContent = up ? "Stop mono" : "Start mono";
}
function wireMonoPower() {
  const btn = document.getElementById("trk-mono-power");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const action = btn.dataset.active === "true" ? "stop" : "start";
    _monoPowerPending = true;
    btn.disabled = true;
    btn.textContent = action === "start" ? "starting…" : "stopping…";
    try {
      await fetch("/api/actions/service", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unit: "garuda-mono", action }),
      });
      if (action === "start") {
        const img = document.getElementById("inference-stream");
        if (img) {
          const mbase = `${location.protocol}//${location.hostname}:8098`;
          setTimeout(() => { img.src = `${mbase}/stream?t=` + Date.now(); }, 2000);
        }
      }
    } catch (e) { /* next health tick corrects the label */ }
    _monoPowerPending = false;
  });
}

function init() {
  wireNav();
  wireRange();
  wireTracking();
  wireMonoPower();
  wireActions();
  wireFollowOverlay();
  wireDepthEngines();
  connectEvents();
  showPanel("overview");
}

document.addEventListener("DOMContentLoaded", init);

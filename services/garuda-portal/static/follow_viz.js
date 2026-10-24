// garuda-portal follow visualisations — a faithful in-browser port of the
// follow control law (services/garuda-follow: follow_controller.py, projection.py,
// sim_kinematics.py). Three views share one core:
//   1. Follow Sim   — synthetic closed loop; the virtual drone flies + converges.
//   2. Live instrument — driven by the REAL FollowCommand from /api/events; the
//      camera boresight is fixed (bench truth), the drone only AIMS at the live
//      subject bearing. No faked flight, no translation.
//   3. Preview maneuver — seeds a virtual world from the locked frame and runs the
//      closed loop forward (labelled simulated), so you can see the fly-in.
// Simulation only — no control decisions live here; the FC/SITL is the real loop.
(function () {
  "use strict";

  // ---- control-law constants (verbatim from follow_controller.py / projection.py) ----
  const HFOV = 1.2, TARGET_SIZE = 6.0, DESIRED_H = 0.5, YAW_GAIN = 2.0, FWD_GAIN = 3.0;
  const DEADZONE = 0.03, MAX_YAW = 0.8, MAX_FWD = 2.0, MIN_H = 0.05, MAX_H = 0.95, MIN_CONF = 0.35;
  const RSTAR = 0.6 / DESIRED_H;      // stand-off range where apparent size == desired (1.2)
  const RADAR_RANGE = 8.5, DT = 1 / 20;

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const dz = (e, d) => Math.abs(e) < d ? 0 : e;
  const wrapPi = a => ((a + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
  const fx = (v, n = 2) => (v < 0 ? "-" : "+") + Math.abs(v).toFixed(n);
  const el = id => document.getElementById(id);

  const C = {
    bg: "#0b0f13", grid: "#1c242c", drone: "#7fd1b9", target: "#e0876b",
    amber: "#e0b06b", ok: "#5ed582", bad: "#ff6b6b", dim: "#6b7a88", ink: "#9fb0bf",
  };
  const MONO = "11px ui-monospace, 'SF Mono', Menlo, monospace";

  // ---- shared control law (follow_controller.compute) ----
  function control(cx, h, conf, present) {
    if (!present) return { valid: false, reason: "no target", yaw: 0, fwd: 0 };
    if (conf < MIN_CONF) return { valid: false, reason: "low confidence " + conf.toFixed(2), yaw: 0, fwd: 0 };
    if (h < MIN_H || h > MAX_H) return { valid: false, reason: "implausible size h=" + h.toFixed(2), yaw: 0, fwd: 0 };
    const ex = dz(cx - 0.5, DEADZONE), eh = DESIRED_H - h;
    return { valid: true, reason: "tracking", yaw: clamp(YAW_GAIN * ex, -MAX_YAW, MAX_YAW), fwd: clamp(FWD_GAIN * eh, -MAX_FWD, MAX_FWD) };
  }
  // projection.project: world target -> bearing/cx/apparent-size, relative to drone yaw
  function project(tx, ty, dx, dy, yaw) {
    const ddx = tx - dx, ddy = ty - dy, range = Math.hypot(ddx, ddy);
    const bearing = wrapPi(Math.atan2(ddy, ddx) - yaw), cx = 0.5 + bearing / HFOV;
    const h = Math.min(MAX_H, TARGET_SIZE / Math.max(range, 0.1) * 0.1);
    const inFov = Math.abs(bearing) < HFOV / 2 && cx >= 0 && cx <= 1;
    return { range, bearing, cx, h, inFov };
  }
  const rangeFromH = h => clamp(0.6 / Math.max(h, 1e-3), 0.3, 40); // inverse of h = 0.6/range
  function stepDrone(s, c, dt) { s.yaw += c.yaw * dt; s.x += c.fwd * Math.cos(s.yaw) * dt; s.y += c.fwd * Math.sin(s.yaw) * dt; }

  // ---- canvas fit (HiDPI) ----
  function fit(cv) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const r = cv.getBoundingClientRect();
    if (r.width === 0) return null;
    cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
    const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w: r.width, h: r.height };
  }

  // ---- reusable top-down plan view (world-frame, drone-centred, north-up) ----
  // drone {x,y,yaw}; target {x,y}|null; command {valid,yaw,fwd,reason}; opts {trail,label}
  function drawPlan(g, drone, target, command, opts) {
    const { ctx, w, h } = g; opts = opts || {};
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 12, sc = R / RADAR_RANGE;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.fillStyle = C.bg; ctx.fill();
    ctx.font = "9px ui-monospace, monospace"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (let m = 2; m <= 8; m += 2) {
      ctx.beginPath(); ctx.arc(cx, cy, m * sc, 0, 7); ctx.strokeStyle = C.grid; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = C.dim; ctx.fillText(m + "m", cx, cy - m * sc);
    }
    const dir = (a, L) => [Math.sin(a) * L, -Math.cos(a) * L];  // world angle -> screen (north-up)
    const toPx = (wx, wy) => [cx + (wy - drone.y) * sc, cy - (wx - drone.x) * sc];
    // FOV cone
    const y = drone.yaw;
    ctx.beginPath(); ctx.moveTo(cx, cy);
    for (let k = -1; k <= 1; k += 2) { const [dx, dy] = dir(y + k * HFOV / 2, R); ctx.lineTo(cx + dx, cy + dy); }
    ctx.closePath();
    const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
    grd.addColorStop(0, "rgba(127,209,185,0.14)"); grd.addColorStop(1, "rgba(127,209,185,0)");
    ctx.fillStyle = grd; ctx.fill();
    // stand-off ring
    ctx.beginPath(); ctx.arc(cx, cy, RSTAR * sc, 0, 7); ctx.setLineDash([5, 4]); ctx.strokeStyle = C.amber; ctx.lineWidth = 1.2; ctx.stroke(); ctx.setLineDash([]);
    // trail
    if (opts.trail) for (let i = 0; i < opts.trail.length; i += 3) {
      const [px, py] = toPx(opts.trail[i].x, opts.trail[i].y);
      ctx.globalAlpha = (i / opts.trail.length) * 0.5; ctx.fillStyle = C.target;
      ctx.beginPath(); ctx.arc(px, py, 1.4, 0, 7); ctx.fill();
    }
    ctx.globalAlpha = 1;
    // target + LOS
    if (target) {
      let [tx, ty] = toPx(target.x, target.y); const dd = Math.hypot(tx - cx, ty - cy); let off = false;
      if (dd > R) { tx = cx + (tx - cx) / dd * R; ty = cy + (ty - cy) / dd * R; off = true; }
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(tx, ty); ctx.strokeStyle = "rgba(224,135,107,0.5)"; ctx.lineWidth = 1; ctx.stroke();
      ctx.beginPath(); ctx.arc(tx, ty, 5, 0, 7); ctx.fillStyle = C.target; ctx.fill();
      if (off) { ctx.fillStyle = C.dim; ctx.font = "8px ui-monospace, monospace"; ctx.fillText("off", tx, ty - 9); }
    }
    // drone (heading triangle)
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(drone.yaw);
    ctx.beginPath(); ctx.moveTo(0, -10); ctx.lineTo(6, 7); ctx.lineTo(0, 3.5); ctx.lineTo(-6, 7); ctx.closePath();
    ctx.fillStyle = C.drone; ctx.shadowColor = C.drone; ctx.shadowBlur = 8; ctx.fill(); ctx.shadowBlur = 0; ctx.restore();
    // HUD
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic"; ctx.font = MONO;
    const valid = command && command.valid;
    ctx.fillStyle = valid ? C.ok : C.bad;
    ctx.fillText(valid ? "TRACKING" : ("HOLD · " + (command ? command.reason : "")), 8, h - 8);
    if (opts.label) { ctx.fillStyle = C.amber; ctx.textAlign = "right"; ctx.fillText(opts.label, w - 8, 14); ctx.textAlign = "left"; }
  }

  // ---- LIVE instrument: fixed boresight (up), drone aims at live bearing ----
  // live {bearing, range, command, present}; drone heading eases toward bearing (rate-limited).
  const liveDrone = { yaw: 0 };
  function drawLive(g, live) {
    const { ctx, w, h } = g;
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 12, sc = R / RADAR_RANGE;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.fillStyle = C.bg; ctx.fill();
    // range rings
    ctx.font = "9px ui-monospace, monospace"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (let m = 2; m <= 8; m += 2) { ctx.beginPath(); ctx.arc(cx, cy, m * sc, 0, 7); ctx.strokeStyle = C.grid; ctx.lineWidth = 1; ctx.stroke(); }
    // fixed camera boresight cone straight up (the bench camera does not move)
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.sin(-HFOV / 2) * R, cy - Math.cos(-HFOV / 2) * R);
    ctx.lineTo(cx + Math.sin(HFOV / 2) * R, cy - Math.cos(HFOV / 2) * R);
    ctx.closePath();
    const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
    grd.addColorStop(0, "rgba(127,209,185,0.10)"); grd.addColorStop(1, "rgba(127,209,185,0)"); ctx.fillStyle = grd; ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy, RSTAR * sc, 0, 7); ctx.setLineDash([5, 4]); ctx.strokeStyle = C.amber; ctx.lineWidth = 1.2; ctx.stroke(); ctx.setLineDash([]);

    const valid = live.present && live.command && live.command.valid;
    // ease the drone heading toward the live bearing, rate-limited by MAX_YAW (aims, doesn't snap)
    if (live.present) {
      const err = wrapPi(live.bearing - liveDrone.yaw);
      liveDrone.yaw += clamp(err, -MAX_YAW * DT, MAX_YAW * DT);
    }
    // target dot at measured bearing (from up) + range proxy
    if (live.present) {
      let r = Math.min(live.range * sc, R);
      const tx = cx + Math.sin(live.bearing) * r, ty = cy - Math.cos(live.bearing) * r;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(tx, ty); ctx.strokeStyle = "rgba(224,135,107,0.5)"; ctx.lineWidth = 1; ctx.stroke();
      ctx.beginPath(); ctx.arc(tx, ty, 5, 0, 7); ctx.fillStyle = C.target; ctx.fill();
    }
    // drone icon (rendered heading = eased aim)
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(liveDrone.yaw);
    ctx.beginPath(); ctx.moveTo(0, -10); ctx.lineTo(6, 7); ctx.lineTo(0, 3.5); ctx.lineTo(-6, 7); ctx.closePath();
    ctx.fillStyle = valid ? C.drone : C.dim; ctx.shadowColor = C.drone; ctx.shadowBlur = valid ? 8 : 0; ctx.fill(); ctx.shadowBlur = 0;
    // commanded forward chevron (closing +, backing -) along drone heading
    if (valid && Math.abs(live.command.fwd) > 0.02) {
      const fl = clamp(live.command.fwd / MAX_FWD, -1, 1) * 22, s = Math.sign(fl);
      ctx.strokeStyle = C.ok; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(0, -12); ctx.lineTo(0, -12 - fl); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-3, -12 - fl + s * 4); ctx.lineTo(0, -12 - fl); ctx.lineTo(3, -12 - fl + s * 4); ctx.stroke();
    }
    ctx.restore();
    // commanded yaw arc (turn intent) around the drone
    if (valid && Math.abs(live.command.yaw) > 0.02) {
      const a0 = -Math.PI / 2, a1 = a0 + live.command.yaw / MAX_YAW * 0.9;
      ctx.strokeStyle = C.ok; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(cx, cy, 15, Math.min(a0, a1), Math.max(a0, a1)); ctx.stroke();
    }
    // HUD
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic"; ctx.font = MONO;
    ctx.fillStyle = valid ? C.ok : C.bad;
    ctx.fillText(valid ? "TRACKING" : ("HOLD · " + (live.command ? live.command.reason : "no target")), 8, h - 8);
    if (valid) {
      const yaw = live.command.yaw, fwd = live.command.fwd;
      const t = yaw > 0.02 ? "turn ▶" : (yaw < -0.02 ? "◀ turn" : "centred");
      const m = fwd > 0.02 ? "close ▲" : (fwd < -0.02 ? "back ▼" : "at range");
      ctx.fillStyle = C.dim; ctx.textAlign = "right"; ctx.fillText(t + "  " + m, w - 8, h - 8); ctx.textAlign = "left";
    }
    ctx.fillStyle = C.dim; ctx.font = "8px ui-monospace, monospace";
    ctx.fillText("commanded intent · camera fixed", 8, 12);
  }

  // ================= Simulation engine =================
  const SIM = { drone: { x: 0, y: 0, yaw: 0 }, target: { x: 6, y: 4 }, mode: "stand", speed: 1.5, occluded: false, trail: [], paused: false };
  function simReset() {
    SIM.drone = { x: 0, y: 0, yaw: 0 }; SIM.target = { x: 6, y: 4 }; SIM.trail = []; SIM.occluded = false;
    simSetMode("stand"); el("sim-occlude") && el("sim-occlude").setAttribute("aria-pressed", "false");
  }
  function simSetMode(m) { SIM.mode = m; document.querySelectorAll("#sim-behavior .seg").forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === m ? "true" : "false")); }
  function simStep() {
    if (SIM.paused) return simState();
    if (SIM.mode !== "stand" && SIM.speed > 0) {
      const dx = SIM.target.x - SIM.drone.x, dy = SIM.target.y - SIM.drone.y, r = Math.hypot(dx, dy) || 1;
      let ux, uy;
      if (SIM.mode === "flee") { ux = dx / r; uy = dy / r; }
      else { const s = SIM.mode === "left" ? 1 : -1; ux = -dy / r * s; uy = dx / r * s; }
      SIM.target.x += ux * SIM.speed * DT; SIM.target.y += uy * SIM.speed * DT;
    }
    const p = project(SIM.target.x, SIM.target.y, SIM.drone.x, SIM.drone.y, SIM.drone.yaw);
    const vis = p.inFov && !SIM.occluded;
    const c = control(vis ? p.cx : 0, vis ? p.h : 0, 0.9, vis);
    stepDrone(SIM.drone, c, DT);
    SIM.trail.push({ x: SIM.target.x, y: SIM.target.y }); if (SIM.trail.length > 120) SIM.trail.shift();
    return { p, c, vis };
  }
  function simState() {
    const p = project(SIM.target.x, SIM.target.y, SIM.drone.x, SIM.drone.y, SIM.drone.yaw);
    const vis = p.inFov && !SIM.occluded;
    return { p, c: control(vis ? p.cx : 0, vis ? p.h : 0, 0.9, vis), vis };
  }
  function updateSimFC(st) {
    const set = (id, v) => { const e = el(id); if (e) e.textContent = v; };
    set("sim-fc-valid", st.c.valid ? "TRACKING" : "HOLD"); const ve = el("sim-fc-valid"); if (ve) ve.className = "follow-badge " + (st.c.valid ? "ok" : "bad");
    set("sim-fc-reason", st.c.reason);
    set("sim-fc-yaw", fx(st.c.yaw)); set("sim-fc-fwd", fx(st.c.fwd));
    set("sim-fc-size", st.vis ? st.p.h.toFixed(2) : "—"); set("sim-fc-range", st.vis ? st.p.range.toFixed(1) : "—");
  }

  // ================= Preview maneuver (seeded from the live locked frame) =================
  const PREVIEW = { active: false, t: 0, drone: null, target: null };
  function startPreview(live) {
    if (!live || !live.present) return;
    // seed a virtual world: drone at origin facing up (+x=north), target placed at the
    // measured bearing + range-proxy from the locked frame; then run the loop forward.
    const b = live.bearing, r = live.range;
    PREVIEW.drone = { x: 0, y: 0, yaw: 0 };
    PREVIEW.target = { x: Math.cos(b) * r, y: Math.sin(b) * r }; // yaw=0 so world bearing == b
    PREVIEW.t = 0; PREVIEW.active = true;
    el("btn-preview") && (el("btn-preview").textContent = "Previewing…");
  }
  function stopPreview() { PREVIEW.active = false; el("btn-preview") && (el("btn-preview").textContent = "Preview maneuver"); }
  function previewStep() {
    const p = project(PREVIEW.target.x, PREVIEW.target.y, PREVIEW.drone.x, PREVIEW.drone.y, PREVIEW.drone.yaw);
    const c = control(p.inFov ? p.cx : 0, p.inFov ? p.h : 0, 0.9, p.inFov);
    stepDrone(PREVIEW.drone, c, DT); PREVIEW.t += DT;
    if (PREVIEW.t > 12) stopPreview();  // ~12 s then release
    return { p, c };
  }

  // ================= live-follow data ingress (called from app.js onFollow) =================
  const LIVE = { present: false, bearing: 0, range: RSTAR, command: null };
  function onLiveFollow(data) {
    const cmd = data.command || {};
    LIVE.command = cmd;
    const tid = cmd.target_id;
    const tracks = data.tracks || [];
    const t = tid != null ? tracks.find(tr => tr.id === tid) : null;
    if (t && Array.isArray(t.bbox) && t.bbox.length === 4) {
      const cxn = t.bbox[0] + t.bbox[2] / 2, hh = t.bbox[3];
      LIVE.bearing = (cxn - 0.5) * HFOV;
      LIVE.range = rangeFromH(hh);
      LIVE.present = !!cmd.valid;
    } else {
      LIVE.present = false;
    }
    // live distance readout (bbox-height estimate; upgrades to stereo-metric once P0 is done)
    const dEl = el("follow-distance");
    if (dEl) dEl.textContent = (LIVE.present && t) ? LIVE.range.toFixed(1) : "—";
    // enable/disable the preview button based on a live lock
    const pv = el("btn-preview"); if (pv) pv.disabled = !(t && cmd.valid);
  }

  // ================= render loop =================
  let liveCanvas, simRadar, simCam, previewOn = false;
  function frame() {
    // Live instrument (in the inference/follow panel) — draw when its canvas is visible
    if (liveCanvas && liveCanvas.offsetParent !== null) {
      const g = fit(liveCanvas);
      if (g) {
        if (PREVIEW.active) {
          const st = previewStep();
          drawPlan(g, PREVIEW.drone, PREVIEW.target, st.c, { label: "PREVIEW · simulated" });
        } else {
          drawLive(g, LIVE);
        }
      }
    }
    // Simulation panel
    if (simRadar && simRadar.offsetParent !== null) {
      const st = simStep();
      const gr = fit(simRadar); if (gr) drawPlan(gr, SIM.drone, SIM.occluded ? null : SIM.target, st.c, { trail: SIM.trail });
      if (simCam) { const gc = fit(simCam); if (gc) drawSimCam(gc, st); }
      updateSimFC(st);
    }
    requestAnimationFrame(frame);
  }

  // compact camera-frame for the sim panel (bbox size == range proxy)
  function drawSimCam(g, st) {
    const { ctx, w, h } = g, X = 6, Y = 6, W = w - 12, Hh = h - 12;
    ctx.clearRect(0, 0, w, h); ctx.fillStyle = "#070b0f"; ctx.fillRect(X, Y, W, Hh);
    const nx = v => X + v * W, ny = v => Y + v * Hh;
    ctx.strokeStyle = "rgba(127,209,185,0.5)"; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(nx(0.5), Y); ctx.lineTo(nx(0.5), Y + Hh); ctx.stroke(); ctx.setLineDash([]);
    ctx.strokeStyle = "rgba(224,176,107,0.7)"; ctx.setLineDash([5, 4]);
    ctx.strokeRect(nx(0.4), ny(0.5 - DESIRED_H / 2), W * 0.2, Hh * DESIRED_H); ctx.setLineDash([]);
    if (st.vis) {
      const cxn = st.p.cx, hh = st.p.h, bw = W * 0.2, bh = Hh * hh, bx = nx(cxn - 0.1), by = ny(0.5 - hh / 2);
      ctx.fillStyle = "rgba(224,135,107,0.15)"; ctx.fillRect(bx, by, bw, bh);
      ctx.strokeStyle = C.target; ctx.lineWidth = 1.5; ctx.strokeRect(bx, by, bw, bh);
      ctx.fillStyle = C.target; ctx.font = "9px ui-monospace, monospace"; ctx.textAlign = "left"; ctx.fillText("person 0.90", bx, by - 3);
    } else {
      ctx.fillStyle = C.dim; ctx.font = MONO; ctx.textAlign = "center"; ctx.fillText("OUT OF FRAME", X + W / 2, Y + Hh / 2); ctx.textAlign = "left";
    }
  }

  // ================= wiring =================
  function wireSimControls() {
    document.querySelectorAll("#sim-behavior .seg").forEach(b => b.addEventListener("click", () => simSetMode(b.dataset.mode)));
    const sp = el("sim-speed"); if (sp) sp.addEventListener("input", e => { SIM.speed = +e.target.value; el("sim-speed-val") && (el("sim-speed-val").textContent = (+e.target.value).toFixed(1)); });
    const oc = el("sim-occlude"); if (oc) oc.addEventListener("click", () => { SIM.occluded = !SIM.occluded; oc.setAttribute("aria-pressed", SIM.occluded ? "true" : "false"); });
    const pa = el("sim-pause"); if (pa) pa.addEventListener("click", () => { SIM.paused = !SIM.paused; pa.textContent = SIM.paused ? "Resume" : "Pause"; });
    el("sim-reset") && el("sim-reset").addEventListener("click", simReset);
  }
  function init() {
    liveCanvas = el("live-plan"); simRadar = el("sim-radar"); simCam = el("sim-cam");
    wireSimControls();
    const pv = el("btn-preview");
    if (pv) pv.addEventListener("click", () => { if (PREVIEW.active) stopPreview(); else startPreview(LIVE); });
    window.addEventListener("resize", () => { }); // fit() runs each frame; nothing to precompute
    simReset();
    requestAnimationFrame(frame);
  }

  window.FollowViz = { init, onLiveFollow };
  if (document.readyState !== "loading") init(); else document.addEventListener("DOMContentLoaded", init);
})();

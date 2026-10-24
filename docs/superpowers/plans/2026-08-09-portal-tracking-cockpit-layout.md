# Tracking Tab Cockpit Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Tracking tab's camera view ~3× larger and fit the whole tab on a 1080p screen with no page scrolling.

**Architecture:** The Tracking tab (only) breaks out of the portal's 960 px column via a `wide` class `showPanel()` toggles on `<main>`. The panel body becomes a two-column grid: flexible left column holding one compact control row + the video stage (height-capped to the viewport), and a fixed ~380 px sidebar holding banners, distance chart, drone plan, follow readout, stats, a scroll-capped detections list, and the stereo-depth block collapsed inside a `<details>`. All element ids are preserved — the JS wiring is id-based and needs only the one-line wide-class toggle.

**Tech Stack:** Vanilla JS SPA (no framework), plain CSS grid/flex, Python stdlib http.server statics, pytest.

**Spec:** `docs/superpowers/specs/2026-08-09-portal-tracking-layout-design.md`

## Global Constraints

- Branch: `feat/portal-tracking`, worktree `/media/bhanu1234/Development/GARUDASDK/.claude/worktrees/feat+portal-tracking` (work continues on the existing unmerged branch — do NOT create a new one).
- Files touched: `services/garuda-portal/static/index.html`, `static/style.css`, `static/app.js`, `tests/test_static_routes.py` only. No backend, no portal.py, no mono changes.
- Every existing element id must survive the restructure (spec §3) — the structural test enforces this.
- Other tabs keep the 960 px `main` cap; only the Tracking tab widens.
- Only the stereo-depth block may be collapsed by default (owner decision); everything else stays visible.
- Pi deploy = `scp` changed statics to `garuda@10.42.0.128:/opt/garuda/garuda-portal/static/` then `ssh garuda@10.42.0.128 sudo systemctl restart garuda-portal`. The Pi runs the deployed copy; the repo worktree is the source of truth — never edit on the Pi.
- Pure UI change: no performance claims anywhere, no benchlog entry (rule 1 not in play). No new dependencies.
- Known pre-existing flake: 2 timing tests in `test_static_routes.py`'s camera-lazy-acquire suite occasionally flicker; if they fail, rerun them in isolation before suspecting this change.

---

### Task 1: Cockpit restructure (markup + CSS + wide toggle, structurally tested)

**Files:**
- Modify: `services/garuda-portal/static/index.html` (replace the `<section id="panel-inference">` block)
- Modify: `services/garuda-portal/static/style.css` (delete `.feed-row` rules; append cockpit block)
- Modify: `services/garuda-portal/static/app.js` (`showPanel()`: one wide-class toggle)
- Test: `services/garuda-portal/tests/test_static_routes.py` (append one structural test)

**Interfaces:**
- Consumes: existing id-based JS wiring (`wireTracking`, `wireFollowOverlay`, `wireDepthEngines`, `showPanel`'s tracking enter/leave branch) — all untouched except the toggle.
- Produces: `main.wide` CSS mode + `.track-grid`/`.track-main`/`.track-side` layout classes; the served `index.html` guaranteed (by test) to contain every live element id.

- [ ] **Step 1: Write the failing structural test**

Append to `services/garuda-portal/tests/test_static_routes.py` (uses the existing `_start_server` helper defined above `test_index_html_contains_all_seven_panels`):

```python
def test_index_html_tracking_panel_is_cockpit_grid():
    # 2026-08-09 layout spec: two-column cockpit; every live id must survive the
    # restructure because ALL tracking JS wiring is id-based (spec §3).
    server = _start_server({"health": lambda: {}}, 8143)
    try:
        body = urllib.request.urlopen("http://127.0.0.1:8143/").read().decode()
        assert 'class="track-grid"' in body
        assert 'class="track-side"' in body
        assert "<details" in body and "Stereo depth" in body
        assert "feed-row" not in body  # old side-by-side row is gone
        for eid in ["follow-stage", "inference-stream", "follow-overlay", "inference-hint",
                    "trk-dist", "trk-engine", "trk-class", "trk-prompt", "trk-cal-truth",
                    "trk-cal-add", "trk-cal-reset", "trk-chart",
                    "mono-offline-banner", "tracking-banner", "tracking-text",
                    "plan-col", "live-plan", "btn-preview",
                    "inf-cpu", "inf-cpu-cores", "inf-temp", "inf-npu-fps", "inf-pcie", "inf-pcie-rate",
                    "follow-panel", "follow-valid", "follow-reason", "follow-target",
                    "follow-distance", "follow-yaw", "follow-forward", "follow-vertical",
                    "btn-follow-auto", "follow-pinned-status", "detections-list",
                    "track-depth-col", "track-depth", "depth-off-note", "depth-eng-status"]:
            assert f'id="{eid}"' in body, f"missing #{eid}"
    finally:
        server.shutdown()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/garuda-portal && python3 -m pytest tests/test_static_routes.py::test_index_html_tracking_panel_is_cockpit_grid -v`
Expected: FAIL on `assert 'class="track-grid"' in body`.

- [ ] **Step 3: Replace the tracking panel markup**

In `static/index.html`, replace the entire `<section id="panel-inference" class="panel hidden">` … `</section>` block (currently the section containing `feed-row`) with:

```html
  <section id="panel-inference" class="panel hidden">
    <h2>Tracking <span class="muted">&middot; garuda-mono (CM3 + YOLO26 + metric depth)</span></h2>
    <div class="track-grid">
      <div class="track-main">
        <div class="range-bar track-controls">
          <span class="range-readout">Distance <strong id="trk-dist">&mdash;</strong></span>
          <label>Depth engine
            <select id="trk-engine"><option value="y26d">y26d</option><option value="da">da</option></select>
          </label>
          <label>Target
            <select id="trk-class">
              <option>person</option><option>bicycle</option><option>car</option><option>motorcycle</option><option>airplane</option><option>bus</option><option>train</option><option>truck</option><option>boat</option>
              <option>traffic light</option><option>fire hydrant</option><option>stop sign</option><option>parking meter</option><option>bench</option><option>bird</option><option>cat</option>
              <option>dog</option><option>horse</option><option>sheep</option><option>cow</option><option>elephant</option><option>bear</option><option>zebra</option><option>giraffe</option><option>backpack</option>
              <option>umbrella</option><option>handbag</option><option>tie</option><option>suitcase</option><option>frisbee</option><option>skis</option><option>snowboard</option><option>sports ball</option>
              <option>kite</option><option>baseball bat</option><option>baseball glove</option><option>skateboard</option><option>surfboard</option><option>tennis racket</option>
              <option>bottle</option><option>wine glass</option><option>cup</option><option>fork</option><option>knife</option><option>spoon</option><option>bowl</option><option>banana</option><option>apple</option>
              <option>sandwich</option><option>orange</option><option>broccoli</option><option>carrot</option><option>hot dog</option><option>pizza</option><option>donut</option><option>cake</option><option>chair</option>
              <option>couch</option><option>potted plant</option><option>bed</option><option>dining table</option><option>toilet</option><option>tv</option><option>laptop</option><option>mouse</option>
              <option>remote</option><option>keyboard</option><option>cell phone</option><option>microwave</option><option>oven</option><option>toaster</option><option>sink</option>
              <option>refrigerator</option><option>book</option><option>clock</option><option>vase</option><option>scissors</option><option>teddy bear</option><option>hair drier</option>
              <option>toothbrush</option>
            </select>
          </label>
          <label>Open-vocab <input id="trk-prompt" placeholder="open-vocab: pen, keys..."></label>
          <label>True dist (m) <input id="trk-cal-truth" type="number" step=".05" min="0"></label>
          <button id="trk-cal-add" type="button">cal</button>
          <button id="trk-cal-reset" type="button">reset</button>
        </div>
        <div id="follow-stage" class="stream-wrap hidden">
          <img id="inference-stream" alt="annotated inference stream">
          <canvas id="follow-overlay" title="click a detected subject to follow"></canvas>
        </div>
        <p id="inference-hint" class="muted hidden">annotated live feed (boxes drawn on the NPU) &mdash; <strong>click a subject on the video to follow</strong>; detections listed in the sidebar</p>
      </div>
      <aside class="track-side">
        <p id="mono-offline-banner" class="banner hidden">mono offline &mdash; retrying</p>
        <div id="tracking-banner" class="tracking-banner hidden"><span class="tb-dot"></span><span id="tracking-text">no target</span></div>
        <canvas id="trk-chart" height="70"></canvas>
        <div id="plan-col" class="plan-col hidden">
          <div class="plan-title">Drone tracking &middot; commanded motion</div>
          <canvas id="live-plan" aria-label="commanded drone motion, top-down" title="The virtual drone aims at the tracked subject. The bench camera is fixed, so this is intent, not flight."></canvas>
          <div class="actions"><button type="button" id="btn-preview" disabled>Preview maneuver</button></div>
          <div class="inf-stats">
            <div class="inf-stat"><span class="is-label">CPU</span><span class="is-val" id="inf-cpu">&mdash;</span><div class="cpu-cores" id="inf-cpu-cores"></div></div>
            <div class="inf-stat"><span class="is-label">Temp</span><span class="is-val" id="inf-temp">&mdash;</span></div>
            <div class="inf-stat"><span class="is-label">NPU rate</span><span class="is-val" id="inf-npu-fps">idle</span></div>
            <div class="inf-stat"><span class="is-label">PCIe link</span><span class="is-val" id="inf-pcie">&mdash;</span></div>
            <div class="inf-stat"><span class="is-label">PCIe in</span><span class="is-val" id="inf-pcie-rate">idle</span></div>
          </div>
        </div>
        <div id="follow-panel" class="follow-panel">
          <div class="follow-readout">
            <span id="follow-valid" class="follow-badge muted">no target</span>
            <span id="follow-reason" class="muted"></span>
            <button type="button" id="btn-follow-auto" class="follow-pin-btn">Auto (unpin)</button>
            <span id="follow-pinned-status" class="muted">auto</span>
          </div>
          <div class="follow-metrics">
            <span>target <strong id="follow-target">none</strong></span>
            <span>distance <strong id="follow-distance">&mdash;</strong> m <span class="muted">(bbox est)</span></span>
            <span>yaw <strong id="follow-yaw">0.00</strong> rad/s</span>
            <span>forward <strong id="follow-forward">0.00</strong> m/s</span>
            <span>vertical <strong id="follow-vertical">0.00</strong> m/s</span>
          </div>
        </div>
        <div class="side-title">Live detections</div>
        <ul id="detections-list" class="detections-scroll"><li class="muted">no detections yet</li></ul>
        <details class="track-depth-details">
          <summary>Stereo depth &middot; OV9281</summary>
          <div id="track-depth-col" class="depth-col hidden">
            <img id="track-depth" class="hidden" alt="live stereo depth map">
            <div id="depth-off-note" class="depth-placeholder">Depth off &mdash; tracking stays light. Pick an engine to start.</div>
            <div class="depth-engines">
              <button type="button" class="deng active" data-eng="off">Off</button>
              <button type="button" class="deng" data-eng="sgbm">SGBM &middot; CPU</button>
              <button type="button" class="deng" data-eng="stereonet">StereoNet &middot; NPU</button>
              <button type="button" class="deng deng-rec" data-eng="fast_depth">fast_depth &middot; NPU &#9733;</button>
            </div>
            <p class="muted lp-cap" id="depth-eng-status"><strong>Off by default</strong> so it doesn't load the CPU while you track. <strong>SGBM</strong> = CPU metric stereo (~2 cores &mdash; the hog). <strong>StereoNet</strong>/<strong>fast_depth</strong> run on the NPU (capped 6&nbsp;fps &mdash; fast_depth&nbsp;&#9733; ~0.4&nbsp;core, the light pick for live). Per-target distance needs the stereo&harr;CM3 calibration (P0).</p>
          </div>
        </details>
      </aside>
    </div>
  </section>
```

Deliberate content changes (everything else is a verbatim move): the two old control bars merge into one `.track-controls` row (cal controls join it, inline `style=` widths dropped — CSS takes over); the chart loses its inline `style=` (CSS takes over); the `<h3>Follow</h3>`/`<h3>Live detections</h3>` headings become the compact `.side-title` label (follow panel needs no heading); the Auto-unpin button moves into the `.follow-readout` row (its old `.actions` wrapper is gone); the drone-plan caption paragraph becomes the `title=` tooltip on `#live-plan`; the depth column's inner `plan-title` div is replaced by the `<details>` summary.

- [ ] **Step 4: CSS — delete `.feed-row`, append the cockpit block**

In `static/style.css`, delete these two rules (the tracking panel was their only consumer):

```css
.feed-row { display: flex; gap: .9rem; align-items: flex-start; flex-wrap: wrap; }
.feed-row .stream-wrap { flex: 2 1 320px; min-width: 280px; width: auto; }
```

Append at the end of the file:

```css
/* --- Tracking cockpit (2026-08-09 layout spec): Tracking tab breaks out of the
   960px column; video left (viewport-height-capped), compact always-visible
   sidebar right; stereo depth is the one collapsed-by-default block. --- */
main.wide { max-width: none; }
.track-grid { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: .8rem; align-items: start; }
.track-main { min-width: 0; }
.track-controls { margin: 0 0 .5rem 0; gap: .6rem; }
.track-controls #trk-prompt { width: 10rem; }
.track-controls #trk-cal-truth { width: 4.5rem; }
/* Fit the video to the leftover viewport height: 210px ≈ header (~95) + h2/controls
   row (~75) + paddings/hint (~40). 16/9 converts the height cap into a width cap. */
main.wide #follow-stage { max-width: min(100%, calc((100vh - 210px) * 16 / 9)); }
#inference-hint { margin: .4rem 0 0 0; }
.track-side { display: flex; flex-direction: column; gap: .55rem; min-width: 0;
  max-height: calc(100vh - 150px); overflow-y: auto; }
.track-side .banner, .track-side .tracking-banner, .track-side .follow-panel { margin: 0; }
.track-side #trk-chart { width: 100%; background: #0b0f13; border: 1px solid #2a3540; border-radius: 8px; }
/* .plan-col/.depth-col carry row-flex sizing from the old .feed-row layout; inside
   the column-flex sidebar that basis would become a height — neutralize it. */
.track-side .plan-col, .track-side .depth-col { flex: none; min-width: 0; }
.track-side #live-plan { max-width: 200px; margin: 0 auto; }
.track-side .actions { margin: 0; }
.side-title { font-size: .72rem; text-transform: uppercase; letter-spacing: .05em; color: #9fb0bf; }
.detections-scroll { max-height: 110px; overflow-y: auto; }
.track-depth-details summary { cursor: pointer; font-size: .72rem; text-transform: uppercase;
  letter-spacing: .05em; color: #9fb0bf; padding: .2rem 0; }
.track-depth-details[open] summary { color: #7fd1b9; }
@media (max-width: 1100px) {
  .track-grid { grid-template-columns: 1fr; }
  .track-side { max-height: none; overflow: visible; }
}
```

- [ ] **Step 5: app.js — wide-class toggle in `showPanel()`**

In `static/app.js`, in `showPanel(name)`, immediately after the `for (const p of PANELS) {...}` loop that toggles panel visibility, add:

```javascript
  // Tracking gets the full viewport width (cockpit layout, 2026-08-09 spec);
  // every other tab keeps the 960px reading column.
  const mainEl = document.querySelector("main");
  if (mainEl) mainEl.classList.toggle("wide", name === "inference");
```

No other JS changes: overlay geometry reads the img's rendered size, the chart reads `clientWidth` per draw, and `follow_viz.js` fits the plan canvas from its rect each frame — all adapt to the new containers automatically.

- [ ] **Step 6: Run the new test to verify it passes**

Run: `cd services/garuda-portal && python3 -m pytest tests/test_static_routes.py::test_index_html_tracking_panel_is_cockpit_grid -v`
Expected: PASS.

- [ ] **Step 7: Run the full portal suite**

Run: `cd services/garuda-portal && python3 -m pytest tests/ -v`
Expected: all pass (84 passed, 1 skipped at time of writing). If either known camera-lazy-acquire timing test fails, rerun it in isolation to confirm it's the pre-existing flake, not this change.

- [ ] **Step 8: Commit**

```bash
git add services/garuda-portal/static/index.html services/garuda-portal/static/style.css \
        services/garuda-portal/static/app.js services/garuda-portal/tests/test_static_routes.py
git commit -m "feat(portal): Tracking-tab cockpit layout — full-width video, compact sidebar"
```

---

### Task 2: Deploy, bench verification, docs close-out

**Files:**
- Modify (docs only): `docs/superpowers/specs/2026-08-09-portal-tracking-layout-design.md` (status line), `CLAUDE.md` (§7, one sentence)

**Interfaces:**
- Consumes: Task 1's committed statics.
- Produces: the layout live on the bench; spec marked implemented.

- [ ] **Step 1: Deploy the statics**

```bash
scp services/garuda-portal/static/index.html services/garuda-portal/static/style.css \
    services/garuda-portal/static/app.js garuda@10.42.0.128:/opt/garuda/garuda-portal/static/
ssh garuda@10.42.0.128 'sudo systemctl restart garuda-portal && sleep 2 && systemctl is-active garuda-portal'
```

Expected: `active`.

- [ ] **Step 2: Smoke-check the served page**

```bash
curl -s http://10.42.0.128:8080/ | grep -c 'track-grid'
curl -s http://10.42.0.128:8080/style.css | grep -c 'main.wide'
```

Expected: both ≥ 1.

- [ ] **Step 3: Owner visual checklist at the browser (1080p)**

Ask the owner to open `http://10.42.0.128:8080/` → Tracking and confirm, per spec §5:
(a) no page scroll with all default panels visible; (b) the video fills the left column (~1300 px wide); (c) click-to-lock lands accurately at all four frame corners; (d) opening "Stereo depth · OV9281" shows working engine buttons; (e) `ssh garuda@10.42.0.128 sudo systemctl stop garuda-mono` → offline banner appears in the sidebar, `... start garuda-mono` → recovers; (f) every other tab still renders in the 960 px column.

Record any misfit (e.g. sidebar clipping, wrong height allowance) and fix the CSS constants (`210px` / `150px` / `380px`) in place, re-deploy, re-check — these three constants are the intended tuning knobs.

- [ ] **Step 4: Docs**

In the spec, change the status line to `Status: implemented 2026-08-09`. In `CLAUDE.md` §7's first (bench state) paragraph, append one sentence: `Tracking tab now uses the full-width cockpit layout (video viewport-fitted left, compact sidebar right — spec docs/superpowers/specs/2026-08-09-portal-tracking-layout-design.md).`

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-08-09-portal-tracking-layout-design.md CLAUDE.md
git commit -m "docs: cockpit layout deployed + verified — spec implemented, CLAUDE.md resume state"
```

---

*(Tasks 3–4 added 2026-08-09 after owner approved the mono power toggle spec,
`docs/superpowers/specs/2026-08-09-portal-mono-power-toggle-design.md`. Task 2's Steps 4–5
(docs + commit) are superseded by Task 4's combined close-out.)*

### Task 3: mono power toggle (whitelist + probes + UI, x86-tested)

**Files:**
- Modify: `services/garuda-configd/whitelist.py` (one entry)
- Modify: `services/garuda-portal/probes.py` (one entry)
- Modify: `services/garuda-portal/static/index.html` (one button)
- Modify: `services/garuda-portal/static/app.js` (button wiring)
- Modify: `services/garuda-portal/static/style.css` (button styling)
- Test: `services/garuda-configd/tests/test_whitelist.py`, `services/garuda-portal/tests/test_probes.py`, `services/garuda-portal/tests/test_static_routes.py`

**Interfaces:**
- Consumes: existing `POST /api/actions/service` route (portal → configd `unit.action`); existing health events (`onHealth(data)` in app.js, `data.services` dict); Task 1's `.track-side` sidebar.
- Produces: `#trk-mono-power` button; `services["garuda-mono"]` in every health payload; `garuda-mono` accepted by configd's whitelist.

- [ ] **Step 1: Write the failing tests**

Append to `services/garuda-configd/tests/test_whitelist.py`:

```python
def test_accepts_garuda_mono_start_stop():
    # mono power toggle (2026-08-09 spec): the Tracking-tab button drives these
    for action in ("start", "stop"):
        ok, _ = validate_request({"op": "unit.action",
                                  "args": {"unit": "garuda-mono", "action": action}})
        assert ok
```

In `services/garuda-portal/tests/test_probes.py`, add to the `FAKE` dict:

```python
    ("systemctl", "is-active", "garuda-mono"): "inactive\n",
```

and to `test_collect_health_parses_probes`:

```python
    assert h["services"]["garuda-mono"] is False
```

In `services/garuda-portal/tests/test_static_routes.py`, add `"trk-mono-power"` to the id list in `test_index_html_tracking_panel_is_cockpit_grid`.

- [ ] **Step 2: Run to verify failures**

Run: `cd services/garuda-configd && python3 -m pytest tests/test_whitelist.py -v` → new test FAILS (garuda-mono not whitelisted).
Run: `cd services/garuda-portal && python3 -m pytest tests/test_probes.py tests/test_static_routes.py::test_index_html_tracking_panel_is_cockpit_grid -v` → both additions FAIL.

- [ ] **Step 3: Implement the two one-line backend changes**

`services/garuda-configd/whitelist.py`:

```python
ALLOWED_UNITS = {"garuda-inference", "garuda-cam-portal", "garuda-ptp4l-client", "garuda-mono"}
```

`services/garuda-portal/probes.py`:

```python
WATCHED_UNITS = ["garuda-portal", "garuda-configd", "garuda-ptp4l-client", "garuda-mono"]
```

- [ ] **Step 4: UI — button markup, styling, wiring**

`static/index.html` — in the Tracking panel's `<aside class="track-side">`, insert as the FIRST child (above `#mono-offline-banner`):

```html
        <button id="trk-mono-power" type="button" class="mono-power" disabled>mono &mdash;</button>
```

`static/style.css` — append to the cockpit block:

```css
.mono-power { width: 100%; font-weight: 600; }
.mono-power[data-active="true"] { border-color: #e0876b; color: #e0876b; }
.mono-power[data-active="false"] { border-color: #7fd1b9; color: #7fd1b9; }
```

`static/app.js` — three edits:

(a) In `onHealth(data)`, after the `renderCpuCores(data.cpu_cores);` line, add:

```javascript
  updateMonoPowerBtn(data.services);
```

(b) Add near `wireTracking()`:

```javascript
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
```

(c) In `init()`, after `wireTracking();`, add `wireMonoPower();`.

- [ ] **Step 5: Run to verify passes**

Same commands as Step 2 → all PASS.

- [ ] **Step 6: Full suites**

Run: `cd services/garuda-configd && python3 -m pytest tests/ -v` and `cd services/garuda-portal && python3 -m pytest tests/ -v` → no regressions (known camera-lazy-acquire flakes: rerun in isolation if they flicker).

- [ ] **Step 7: Commit**

```bash
git add services/garuda-configd/whitelist.py services/garuda-configd/tests/test_whitelist.py \
        services/garuda-portal/probes.py services/garuda-portal/tests/test_probes.py \
        services/garuda-portal/tests/test_static_routes.py services/garuda-portal/static/
git commit -m "feat(portal): Tracking-tab mono power toggle via configd whitelist"
```

---

### Task 4: deploy toggle, owner verification, combined docs close-out

**Files:**
- Modify (docs only): both 2026-08-09 spec status lines, `CLAUDE.md` §7, `docs/decisions.md`

- [ ] **Step 1: Deploy**

```bash
scp services/garuda-configd/whitelist.py garuda@10.42.0.128:/opt/garuda/garuda-configd/whitelist.py
scp services/garuda-portal/probes.py garuda@10.42.0.128:/opt/garuda/garuda-portal/probes.py
scp services/garuda-portal/static/index.html services/garuda-portal/static/style.css \
    services/garuda-portal/static/app.js garuda@10.42.0.128:/opt/garuda/garuda-portal/static/
ssh garuda@10.42.0.128 'sudo systemctl restart garuda-configd garuda-portal && sleep 2 \
  && systemctl is-active garuda-configd garuda-portal'
```

Expected: both `active`.

- [ ] **Step 2: API smoke (mono stays OFF at the end — power-limited bench)**

```bash
curl -s -X POST http://10.42.0.128:8080/api/actions/service \
  -H 'Content-Type: application/json' -d '{"unit":"garuda-mono","action":"start"}'
sleep 3; curl -s http://10.42.0.128:8080/api/health | python3 -c "import json,sys; print(json.load(sys.stdin)['services'])"
curl -s -X POST http://10.42.0.128:8080/api/actions/service \
  -H 'Content-Type: application/json' -d '{"unit":"garuda-mono","action":"stop"}'
```

Expected: start returns ok, health shows `'garuda-mono': True`, stop returns ok.

- [ ] **Step 3: Owner browser test** — Tracking tab: button reads "Start mono" (mono off) → click → "starting…" → video + tracks resume within a few seconds → click "Stop mono" → offline banner appears, button back to "Start mono". Plus the outstanding layout item: other tabs still render in the narrow 960 px column. Record the owner's confirmations.

- [ ] **Step 4: Combined docs close-out**

Spec status lines → `implemented 2026-08-09` (both the cockpit-layout spec and the mono-power-toggle spec). `CLAUDE.md` §7: update the bench-state paragraph — cockpit Tracking layout live; mono power toggle live (configd whitelist grew by garuda-mono); power root cause CLOSED: measured negotiated PSU contract 3000 mA + owner-confirmed 5 V⎓3 A charger label = 15 W ceiling, four brownouts on 2026-08-09 with direct load-ramp cause-effect, fix = official 27 W (5 V⎓5 A) supply pending purchase; note the new follow-up: `MonoClient` subscribes permanently so mono never idles while the portal runs (lazy-subscribe fix proposed, not implemented). `docs/decisions.md`: dated entry with the same power root-cause facts (symptom → evidence → verdict → fix pending; note "do NOT set PSU_MAX_CURRENT=5000").

- [ ] **Step 5: Commit**

```bash
git add docs/ CLAUDE.md
git commit -m "docs: cockpit + mono-toggle close-out — specs implemented, power root-cause recorded"
```

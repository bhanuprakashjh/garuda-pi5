# Portal Tracking tab — cockpit layout redesign

Date: 2026-08-09 · Status: implemented 2026-08-09 (owner-verified on bench) · Branch: `feat/portal-tracking`
(continuation — UI polish on the converged Tracking tab)

## Problem

The Tracking tab renders the camera small and scrolls a lot. Two causes: `main` is capped at
`max-width: 960px` for every tab, and inside that cap the video shares `.feed-row` with two side
columns, leaving ~460 px for the stream. Everything else (two control bars, distance chart, follow
panel, detections list) stacks vertically below, so on the owner's 1080p display the page needs
heavy scrolling and the camera — the point of the tab — is the smallest element on it.

## Decision (owner-approved)

**Cockpit two-column layout, Tracking tab only** (option A of three presented). The tab breaks out
of the 960 px cap; the video takes a flexible left column sized to fill the remaining viewport
height; everything else lives in a compact fixed-width right sidebar. Per owner: only the stereo
depth block may be collapsed by default — drone plan, stats, follow readout, and detections stay
always-visible. Target display: 1080p (1920×1080). Other tabs are untouched.

## 1. Scope

`services/garuda-portal/static/` only — `index.html` (restructure the tracking panel markup),
`style.css` (grid + sidebar rules), `app.js` (one small change: `showPanel()` toggles a wide-mode
class). No backend, portal.py, API, or mono changes. Deploy = scp statics to
`/opt/garuda/garuda-portal/static/` + `sudo systemctl restart garuda-portal`.

## 2. Layout

- `showPanel()` adds `wide` to `<main>`'s class list while the active panel is `inference`,
  removes it otherwise. CSS: `main.wide { max-width: none; }` — the 960 px cap stays for all
  other tabs.
- The tracking panel body becomes a grid: flexible left column (`minmax(0, 1fr)`) + fixed
  sidebar (~380 px), single `gap`.
- **Left column:** one wrapping control row — distance readout, depth-engine select, target-class
  select, open-vocab prompt input, cal truth/add/reset — all existing elements, existing ids —
  then the video stage (`#follow-stage` + `#follow-overlay`). The stage is capped by both column
  width and viewport height so the whole page fits without scrolling:
  `max-width: min(100%, calc((100vh - 210px) * 16 / 9))` on the stage — 210 px ≈ header (~95)
  + control row (~50) + padding/gaps/hint (~65), verified against the rendered header at
  implementation; tuned to 210 px at implementation (hint line included in the allowance). At 1920×1080 the video lands ≈1300 px wide (vs ~460 px today).
- **Right sidebar, top to bottom:** mono-offline banner, tracking-status banner, distance chart
  (`#trk-chart`), drone plan (`#live-plan` + Preview button), follow readout (badge + compact
  metrics + auto/unpin), `inf-stats` rows, detections list (capped height, internal scroll),
  and the stereo depth block (`#track-depth-col` contents) inside a `<details>` collapsed by
  default — summary "Stereo depth · OV9281".
- **Fallback:** below ~1100 px viewport width the grid collapses to a single column (video first,
  sidebar content stacked under it) so small windows stay usable.

## 3. Behavior invariants

- Every element id is preserved; all JS wiring is id-based and untouched apart from the
  `showPanel()` wide-class toggle.
- Follow-overlay click accuracy: the overlay canvas already derives its size from the img's
  rendered `clientWidth/clientHeight` and redraws on window resize — normalized hit-testing is
  size-agnostic, so click-to-lock keeps working at the larger size.
- The distance chart sets `canvas.width = clientWidth` on every draw — it adapts to sidebar width
  with no JS change.
- JS visibility toggling of `#follow-stage`/`#plan-col`/`#track-depth-col` (`hidden` class on
  stream start/stop) must keep working inside the new containers; the `<details>` wrapper only
  adds owner-controlled collapse around the depth block, it does not replace the `hidden` logic.
- Compute-idle rules unchanged: mono stream src still set on tab enter / removed on leave; the
  `<details>` state has no effect on which engines run (buttons merely sit in a collapsed region).

## 4. Failure modes

- Mono down: offline banner sits at the top of the sidebar, video area shows the broken-stream
  state as today; auto-retry behavior unchanged.
- Window too small for the grid: single-column fallback, page scrolls — degraded but complete.
- `<details>` collapsed while a stereo engine is active: engine keeps running (state lives
  server-side); the summary row is always visible so it can be reopened.

## 5. Testing

- x86: existing portal suite stays green (`python3 -m pytest services/garuda-portal/tests -v`) —
  no route or backend changes, statics still served.
- Bench visual check at 1080p: (a) no page scroll with all default panels visible; (b) video
  fills the left column; (c) click-to-lock accurate at all four frame corners; (d) depth-engine
  buttons work inside the opened `<details>`; (e) `systemctl stop garuda-mono` → offline banner
  visible in sidebar, `start` → recovers; (f) other tabs unchanged at 960 px.
- Pure UI change — no performance claims, no benchlog entry required (rule 1 not in play).

## 6. Licences

No new dependencies; project licence only.

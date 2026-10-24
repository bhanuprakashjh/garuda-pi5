# Portal mono power toggle — design spec

Date: 2026-08-09 · Status: implemented 2026-08-09 (owner-verified on bench) · Branch: `feat/portal-tracking`

## Problem

The bench's marginal 5 V/3 A supply (measured: negotiated PSU contract 3000 mA; owner-confirmed
from the charger label) browns the Pi out under mono's NPU+camera load — four hard power-cuts on
2026-08-09 alone. Compounding it, mono never idles while the portal runs: `MonoClient` holds a
permanent SSE subscription to mono's `/api/events`, and SSE subscribers count as viewers, so mono
goes hot at boot with nobody watching. Until the 27 W (5 V/5 A) supply arrives, the owner needs a
manual mono on/off control at the point of use — the Tracking tab. (The lazy-`MonoClient` fix is a
separate follow-up, out of scope here.)

## Decision (owner-approved; mechanism improved during design)

A **"Start/Stop mono" button in the Tracking sidebar**, routed through the EXISTING service-control
path: portal `POST /api/actions/service` → `garuda-configd` (the root-side whitelist executor,
"THE security surface") → `systemctl start|stop garuda-mono`. The originally sketched
sudo-from-portal mechanism is rejected — it would bypass configd. No new portal backend route; the
whole feature is one whitelist entry, one watched-units entry, and UI wiring.

## Components

1. **configd whitelist** (`services/garuda-configd/whitelist.py`): add `"garuda-mono"` to
   `ALLOWED_UNITS`. Actions remain the existing start/stop/restart set. Unit test added.
2. **Health probes** (`services/garuda-portal/probes.py`): add `"garuda-mono"` to `WATCHED_UNITS`
   so the portal's 2 s health events carry `services["garuda-mono"]` — that is the button's state
   source (no new polling). Test updated.
3. **UI** (`static/index.html` / `app.js` / `style.css`): button `#trk-mono-power` at the top of
   the Tracking sidebar, above the offline banner. Label/state from the latest health event:
   active → "Stop mono", inactive → "Start mono", no data yet → disabled "mono —". Click →
   `POST /api/actions/service` with `{"unit": "garuda-mono", "action": "stop"|"start"}` (portal
   origin, existing route), button shows "starting…/stopping…" and ignores health updates while
   the request is in flight; after a start, the mono stream `<img src>` is re-set ~2 s later so
   video resumes without a manual refresh. The next health tick (≤2 s) settles the true state.

## Failure modes

- configd down / action rejected: the route returns the error JSON; the button label is corrected
  by the next health event (worst case one 2 s tick of stale label). No retry loop.
- mono fails to start: health keeps reporting inactive → button returns to "Start mono"; the
  existing offline banner stays up. Journal on the Pi is the diagnostic path.
- Pi browns out mid-action: same as any outage — portal gone, browser shows connection loss.

## Security

The control rides configd's whitelist — the one audited surface for unit actions; the whitelist
grows by exactly one CM3-only perception service (no FC/motor path — rule 12 untouched). The
portal itself remains unauthenticated pre-Phase-8; this button is in the same exposure class as
the existing calibrate/detector/service POSTs and is covered by the same pre-Phase-8 auth gate.

## Testing

- x86: whitelist accepts `garuda-mono` start/stop (new test); probes health includes
  `garuda-mono` (test updated); structural index test includes `trk-mono-power`.
- Bench: button stop → mono inactive + offline banner; button start → mono active, banner clears,
  video resumes. Owner-verified.

## Licences

No new dependencies; project licence only.

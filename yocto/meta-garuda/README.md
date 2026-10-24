# meta-garuda

Yocto layer for the **Hailo-15H product tier**. This layer is deliberately *not* built or
used on the Pi 5 bench — the bench runs Raspberry Pi OS with apt packaging for iteration
speed (see `docs/decisions.md`, 2026-07-29 host-strategy entry).

## Rules

- **Recipe-per-dependency**: every dependency added to a Garuda service also gets a skeleton
  `.bb` recipe here (name, version, `LICENSE`, `SRC_URI` stub — unbuilt is fine). This is
  enforced by CLAUDE.md §6 rule 6. The goal: Phase 9 inherits a half-built layer, not a
  greenfield.
- **Release pin**: `LAYERSERIES_COMPAT` is a placeholder (`scarthgap`). Pin it to the Yocto
  release Hailo OS actually ships at port time. If Hailo's Kirkstone→Scarthgap migration has
  landed by then, target Scarthgap only — never port twice.
- **Licence field is load-bearing**: the recipe `LICENSE` fields double as the product-tier
  licence manifest. No GPLv3, same as everywhere else in this repo.

## End-state packaging (two packagings, one codebase)

- **Dev tier (Pi 5)**: pi-gen image with Garuda apt packages preinstalled (Phase 8) — not Yocto.
- **Product tier (Hailo-15H SoM)**: Hailo OS BSP + this layer + swapped capture backend
  (libcamera/RP1 → Hailo Media Library) + recompiled HEFs.

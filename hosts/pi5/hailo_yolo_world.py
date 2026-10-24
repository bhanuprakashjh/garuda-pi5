#!/usr/bin/env python3
"""HailoYoloWorld — open-vocabulary (prompt-driven) detection on the Hailo-10H.

yolo_world_v2s takes TWO inputs: the 640x640 image and a runtime class-embedding tensor
`(1, 80, 512)` — 80 FIXED slots of CLIP ViT-B/32 text features. Prompts are embedded on the Pi CPU
(CLIP text tower via onnxruntime), L2-normalised exactly like YOLO-World's language backbone, and
written into the first N slots; the remaining slots are ZERO-filled (measured better than a ' '
background embedding — see set_prompt_text).
Output is post-NMS on-chip (HAILO NMS BY CLASS) — HailoRT hands back one (n,5) array per slot.

Resolved on-device 2026-08-07 (garuda@10.42.0.128, HailoRT 5.3.0) — see models/yolo_world_v2s_h10.md:
  * the embedding input IS quantised: qp_scale=2.6535412e-05, qp_zp=9778, limvals -0.2595..0.6100
    (a range only L2-normalised CLIP text features fit) — so we do NOT hand-pack uint16;
  * `infer_model.input(emb).set_format_type(FormatType.FLOAT32)` is accepted and HailoRT does the
    quantisation itself, which is the path used here.

LICENCE: YOLO-World (AILab-CVC) GPL-3.0 — bench/eval, not-product-cleared. EVAL-ONLY: `models/`
         zone, never `services/`, never ships.
         CLIP text tower (OpenAI, MIT) via onnxruntime (MIT) + tokenizers (Apache-2.0).
"""
from __future__ import annotations

import os
import numpy as np

try:
    from target_range import Detection
except Exception:
    from collections import namedtuple
    Detection = namedtuple("Detection", "class_id label conf x y w h")

IN_SIZE = 640
N_SLOTS = 80              # compiled-in slot count of input_layer2 — not negotiable at runtime
EMB_DIM = 512             # CLIP ViT-B/32 text width
BG_PROMPT = " "           # YOLO-World's own background/negative class (demo/simple_demo.py)

MODELS = os.environ.get("GARUDA_MODELS", "/home/garuda/garuda-sdk-deploy/models")
CLIP_ONNX = os.environ.get("GARUDA_CLIP_ONNX", os.path.join(MODELS, "clip_vitb32_text_model.onnx"))
CLIP_TOK = os.environ.get("GARUDA_CLIP_TOKENIZER", os.path.join(MODELS, "clip_vitb32_tokenizer.json"))

_emb_cache: dict[str, np.ndarray] = {}     # prompt text -> (512,) L2-normalised float32


def embed_prompts(labels, onnx_path=None, tok_path=None):
    """Prompt strings -> (n, 512) float32 L2-normalised CLIP text features (+ the background row).

    Mirrors YOLO-World's `HuggingFaceCLIPLanguageBackbone.forward`: raw class name (NO "a photo of"
    template), `get_text_features` (i.e. the projected 512-d head), then L2 normalisation.
    The onnxruntime session is opened only for uncached prompts and released immediately — the
    fp32 text tower is ~250 MB and must not sit resident next to the NPU models.
    """
    wanted = [t for t in list(labels) + [BG_PROMPT] if t not in _emb_cache]
    if wanted:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(tok_path or CLIP_TOK)
        so = ort.SessionOptions()
        so.log_severity_level = 3
        # the Pi 5 is passively cooled (CLAUDE.md §2) and this runs beside the capture thread +
        # the NPU loop — never let ORT grab all four cores.
        so.intra_op_num_threads = int(os.environ.get("GARUDA_CLIP_THREADS", "2"))
        so.inter_op_num_threads = 1
        sess = ort.InferenceSession(onnx_path or CLIP_ONNX, so, providers=["CPUExecutionProvider"])
        try:
            for text in wanted:
                ids = np.asarray([tok.encode(text).ids[:77]], np.int64)
                out = sess.run(["text_embeds"], {"input_ids": ids})[0][0].astype(np.float32)
                n = float(np.linalg.norm(out))
                _emb_cache[text] = out / n if n > 0 else out
        finally:
            del sess
    return (np.stack([_emb_cache[t] for t in labels]) if len(labels) else
            np.zeros((0, EMB_DIM), np.float32)), _emb_cache[BG_PROMPT]


def fill_slots(labels, embeddings, pad_embedding=None):
    """Prompt embeddings -> the full (N_SLOTS, 512) tensor the HEF demands.

    Unused slots default to ZERO (measured better than a real filler embedding — see
    HailoYoloWorld.set_prompt_text). Pure/no-hardware: unit-tested on x86 CI.
    """
    labels = list(labels)
    emb = np.asarray(embeddings, np.float32)
    if len(labels) == 0 or len(labels) > N_SLOTS:
        raise ValueError(f"need 1..{N_SLOTS} prompts, got {len(labels)}")
    if emb.shape != (len(labels), EMB_DIM):
        raise ValueError(f"embeddings must be {(len(labels), EMB_DIM)}, got {emb.shape}")
    pad = (np.zeros(EMB_DIM, np.float32) if pad_embedding is None
           else np.asarray(pad_embedding, np.float32).reshape(EMB_DIM))
    full = np.repeat(pad[None, :], N_SLOTS, axis=0)
    full[:len(labels)] = emb
    return full


def set_prompt_checked(det, labels, embeddings, pad_embedding=None, probe=None):
    """Stage a vocabulary onto `det` and PROVE it with `probe` before keeping it.

    On ANY failure — validation, the tensor build, or the probe inference — `det`'s previous
    vocabulary is restored EXACTLY and the exception re-raised. That is what stops a caller which
    reports the old prompt (because it saw the failure) from diverging from what the detector is
    actually running: without this, a failed *re*-prompt leaves `.labels`/`._emb` holding the new,
    unconfirmed vocabulary forever while the snapshot still advertises the old one.

    Duck-typed on `.labels` / `._emb` / `.set_prompt()` / `.infer()`, so it is unit-testable on x86
    with a fake detector — no HEF, no NPU.
    """
    saved_labels, saved_emb = list(det.labels), det._emb.copy()
    try:
        det.set_prompt(labels, embeddings, pad_embedding)
        if probe is not None:
            det.infer(probe)
    except Exception:
        det.labels, det._emb = saved_labels, saved_emb
        raise
    return det


def decode_nms_by_class(by_class, labels, orig_w, orig_h, lb, conf=0.0, net_w=IN_SIZE, net_h=IN_SIZE):
    """HailoRT HAILO_NMS_BY_CLASS output -> [Detection] in original-image pixels.

    `by_class` is what `bindings.output(name).get_buffer()` returns: one (n, 5) array per class
    slot, rows `(y_min, x_min, y_max, x_max, score)` normalised 0..1 against the letterboxed net
    input. `lb` is the (scale, pad_x, pad_y) used to build that letterbox. Slots beyond `labels`
    are ignored — they are the zero-filled padding. Pure/no-hardware: unit-tested on x86 CI.
    """
    r, px, py = lb
    dets = []
    for cid, rows in enumerate(list(by_class)[:len(labels)]):
        rows = np.asarray(rows, np.float32)
        if rows.size == 0:
            continue
        for y0, x0, y1, x1, sc in rows.reshape(-1, 5):
            if sc < conf:
                continue
            bx0 = min(max((x0 * net_w - px) / r, 0.0), orig_w)
            by0 = min(max((y0 * net_h - py) / r, 0.0), orig_h)
            bx1 = min(max((x1 * net_w - px) / r, 0.0), orig_w)
            by1 = min(max((y1 * net_h - py) / r, 0.0), orig_h)
            if bx1 <= bx0 or by1 <= by0:
                continue
            dets.append(Detection(cid, labels[cid], float(sc), bx0, by0, bx1 - bx0, by1 - by0))
    dets.sort(key=lambda d: -d.conf)
    return dets


class HailoYoloWorld:
    """Same shape as HailoYolo26: __init__(hef, vdevice, conf_thresh) + .infer(bgr) -> [Detection].

    `.set_prompt(labels, embeddings, pad_embedding)` swaps the vocabulary with no reconfigure.
    """

    def __init__(self, hef_path, vdevice=None, conf_thresh=0.3, timeout_ms=5000,
                 max_per_class=50):
        from hailo_platform import VDevice, FormatType
        self.conf = conf_thresh
        self.timeout_ms = timeout_ms
        self._own = vdevice is None
        self.vd = vdevice if vdevice is not None else VDevice(VDevice.create_params())
        self.im = self.vd.create_infer_model(str(hef_path))
        self.im.set_batch_size(1)
        # the 512-wide input is the embedding branch; the other one is the image
        self.emb_name = next(n for n in self.im.input_names
                             if tuple(self.im.input(n).shape)[-1] == EMB_DIM)
        self.img_name = next(n for n in self.im.input_names if n != self.emb_name)
        self.oname = self.im.output_names[0]
        # float32 both ways -> HailoRT applies the embedding branch's quant (scale/zp) for us
        self.im.input(self.emb_name).set_format_type(FormatType.FLOAT32)
        self.im.output(self.oname).set_format_type(FormatType.FLOAT32)
        for setter, val in (("set_nms_score_threshold", conf_thresh),
                            ("set_nms_max_proposals_per_class", max_per_class)):
            try:                                   # trims the on-chip NMS output; optional
                getattr(self.im.output(self.oname), setter)(val)
            except Exception:
                pass
        self.cm = self.im.configure()
        self.in_h, self.in_w = tuple(self.im.input(self.img_name).shape)[:2]
        self.out_shape = tuple(self.im.output(self.oname).shape)
        self.emb_shape = tuple(self.im.input(self.emb_name).shape)
        self.labels: list[str] = []
        self._emb = np.zeros(self.emb_shape, np.float32)

    # ---- vocabulary -------------------------------------------------------
    def set_prompt(self, labels, embeddings, pad_embedding=None):
        """labels: list[str] (<= 80); embeddings: (len(labels), 512) float32, L2-normalised."""
        full = fill_slots(labels, embeddings, pad_embedding)
        self._emb = np.ascontiguousarray(full.reshape(self.emb_shape), np.float32)
        self.labels = list(labels)

    def set_prompt_text(self, labels, pad="zeros"):
        """Convenience: embed the prompts on the CPU here, then set them.

        pad="zeros" (default) is a MEASURED choice, not a guess: on /tmp/bus.jpg with
        ["person","bus","backpack","pen","keys"], zero-filled slots give bus 0.922 / person 0.898
        — matching YOLO26's COCO head (0.93 / 0.93) — whereas filling the 75 unused slots with the
        ' ' background embedding drags the same detections down to bus 0.819 / person 0.399 and
        emits a phantom whole-image box in every padded slot. The zoo calibrated this input with all
        80 slots holding real COCO embeddings, so a highly-correlated filler competes; zero does not.
        """
        emb, bg = embed_prompts(labels)
        self.set_prompt(labels, emb, bg if pad == "background" else None)

    # ---- inference --------------------------------------------------------
    def infer(self, bgr):
        import cv2
        if not self.labels:
            raise RuntimeError("no prompt set — call set_prompt()/set_prompt_text() first")
        if bgr.ndim == 2:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        h, w = bgr.shape[:2]
        r = min(self.in_w / w, self.in_h / h)
        nw, nh = round(w * r), round(h * r)
        px, py = (self.in_w - nw) // 2, (self.in_h - nh) // 2
        inp = np.full((self.in_h, self.in_w, 3), 114, np.uint8)
        inp[py:py + nh, px:px + nw] = cv2.resize(bgr, (nw, nh))
        inp = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB)
        b = self.cm.create_bindings()
        b.input(self.img_name).set_buffer(np.ascontiguousarray(inp, np.uint8))
        b.input(self.emb_name).set_buffer(self._emb)
        b.output(self.oname).set_buffer(np.zeros(self.out_shape, np.float32))
        self.cm.run([b], self.timeout_ms)
        return decode_nms_by_class(b.output(self.oname).get_buffer(), self.labels, w, h,
                                   (r, px, py), self.conf, self.in_w, self.in_h)

    def close(self):
        try:
            self.cm.shutdown()
        except Exception:
            pass
        if self._own:
            try:
                self.vd.release()
            except Exception:
                pass


if __name__ == "__main__":
    import sys, time, cv2
    hef = sys.argv[1] if len(sys.argv) > 1 else os.path.join(MODELS, "yolo_world_v2s_h10.hef")
    imgp = sys.argv[2] if len(sys.argv) > 2 else "/tmp/probe.jpg"
    words = [w.strip() for w in (sys.argv[3] if len(sys.argv) > 3 else "person").split(",") if w.strip()]
    img = cv2.imread(imgp)
    if img is None:
        raise SystemExit(f"cannot read {imgp}")
    det = HailoYoloWorld(hef, conf_thresh=float(os.environ.get("CONF", "0.05")))
    print(f"img {img.shape} | net {det.in_w}x{det.in_h} | emb {det.emb_shape} | out {det.out_shape}")
    t0 = time.monotonic(); det.set_prompt_text(words); t_emb = time.monotonic() - t0
    print(f"prompts {words} embedded in {t_emb*1000:.0f} ms "
          f"(|e|={[round(float(np.linalg.norm(_emb_cache[w])), 3) for w in words]}, "
          f"range {min(_emb_cache[w].min() for w in words):.3f}..{max(_emb_cache[w].max() for w in words):.3f})")
    det.infer(img)                                    # warm-up
    t0 = time.monotonic(); out = det.infer(img); dt = time.monotonic() - t0
    print(f"infer {dt*1000:.0f} ms -> {len(out)} detections")
    for d in out[:15]:
        print(f"  {d.label:14s} {d.conf:.3f}  ({d.x:.0f},{d.y:.0f},{d.w:.0f},{d.h:.0f})")

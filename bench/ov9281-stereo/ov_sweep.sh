#!/usr/bin/env bash
# OV9281 pixel-density -> ORB-SLAM3 front-end cost sweep.
# Runs the same rectified OV9281 clip (room1_rect, 1280x800) at 3 resolutions,
# nFeatures fixed at 1200 (isolates the per-pixel extraction/pyramid cost).
# Tracking does NOT converge on the OV9281 pair (known hardware descriptor-match
# failure) -> this measures front-end COMPUTE vs pixels, not accuracy.
set -u
SRC=/home/garuda/euroc_live/room1_rect
YAML=/home/garuda/garuda-sdk-deploy/hosts/pi5/orbslam3/garuda_rect.yaml
WORK=/home/garuda/ovsweep
RES=/home/garuda/ov_sweep_result.txt
IMG=garuda/orbslam3:bench
: > "$RES"
rm -rf "$WORK"; mkdir -p "$WORK"
echo "garuda-depth: $(systemctl is-active garuda-depth 2>/dev/null)" >> "$RES"
echo "temp_before: $(vcgencmd measure_temp 2>/dev/null) throttled: $(vcgencmd get_throttled 2>/dev/null)" >> "$RES"

build_clip () {  # $1 = scale
python3 - "$1" "$SRC" "$WORK" "$YAML" <<'PY'
import sys, os, glob, shutil, re, cv2
s=float(sys.argv[1]); SRC=sys.argv[2]; WORK=sys.argv[3]; YAML=sys.argv[4]
W=round(1280*s); H=round(800*s)
d=os.path.join(WORK, f"s{s:.2f}")
for cam in ("cam0","cam1"):
    dst=os.path.join(d,"mav0",cam,"data"); os.makedirs(dst, exist_ok=True)
    for f in glob.glob(os.path.join(SRC,"mav0",cam,"data","*.png")):
        img=cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        if s!=1.0:
            img=cv2.resize(img,(W,H),interpolation=cv2.INTER_AREA)
        cv2.imwrite(os.path.join(dst, os.path.basename(f)), img)
shutil.copy(os.path.join(SRC,"times.txt"), os.path.join(d,"times.txt"))
out=[]
for L in open(YAML).read().splitlines():
    hit=False
    for key in ("Camera1.fx","Camera1.fy","Camera1.cx","Camera1.cy",
                "Camera2.fx","Camera2.fy","Camera2.cx","Camera2.cy"):
        m=re.match(rf"^{re.escape(key)}:\s*([0-9.]+)", L)
        if m:
            out.append(f"{key}: {float(m.group(1))*s:.8f}"); hit=True; break
    if hit: continue
    if L.startswith("Camera.width:"):  out.append(f"Camera.width: {W}");  continue
    if L.startswith("Camera.height:"): out.append(f"Camera.height: {H}"); continue
    out.append(L)
open(os.path.join(d,"cam.yaml"),"w").write("\n".join(out)+"\n")
print(f"{W}x{H}  {W*H/1e6:.3f}MP")
PY
}

for s in 1.0 0.75 0.5; do
  dims=$(build_clip "$s")
  d="$WORK/s$(printf '%.2f' "$s")"
  echo "=== SCALE $s  $dims ===" >> "$RES"
  docker run --rm -v "$d":/data:ro "$IMG" bash -lc "
    cd /tmp && /opt/ORB_SLAM3/Examples/Stereo/stereo_euroc \
      /opt/ORB_SLAM3/Vocabulary/ORBvoc.txt /data/cam.yaml /data /data/times.txt ov 2>&1 |
      grep -iE 'median tracking|mean tracking|GARUDA_TRACK_FPS|Map .* has|Fail to track'" >> "$RES" 2>&1
done
echo "temp_after: $(vcgencmd measure_temp 2>/dev/null) throttled: $(vcgencmd get_throttled 2>/dev/null)" >> "$RES"
echo "DONE_SWEEP rc=0" >> "$RES"

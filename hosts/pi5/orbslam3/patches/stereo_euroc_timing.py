# Clean-room patch: make ORB-SLAM3's stereo_euroc PRINT the per-frame tracking-time summary.
# The binary already collects vTimesTrack per frame but this ORB-SLAM3 revision never prints it,
# and wall-clock is useless as a speed metric because stereo_euroc.cc usleep()s to play the
# sequence back at its real-time 20 fps. This adds median/mean tracking time (the true front-end
# rate) before main()'s return. Applied at image-build time; idempotent (asserts if already done).
f = "Examples/Stereo/stereo_euroc.cc"
s = open(f).read()
patch = (
    "    // ---- Garuda: per-frame tracking-time stats (the real metric; wall-clock is contaminated\n"
    "    // by the real-time playback usleep above) ----\n"
    "    std::sort(vTimesTrack.begin(), vTimesTrack.end());\n"
    "    { float ttot=0; for(float t: vTimesTrack) ttot+=t; int nT=vTimesTrack.size();\n"
    "      std::cout << \"median tracking time: \" << vTimesTrack[nT/2] << std::endl;\n"
    "      std::cout << \"mean tracking time: \" << ttot/nT << std::endl;\n"
    "      std::cout << \"GARUDA_MEAN_MS=\" << (ttot/nT)*1000.0 << \" GARUDA_TRACK_FPS=\" << nT/ttot << std::endl; }\n"
)
if "GARUDA_MEAN_MS" in s:
    print("stereo_euroc.cc already has the timing print — skipping")
else:
    s2 = s.replace("    return 0;\n}", patch + "    return 0;\n}", 1)
    assert s2 != s, "anchor 'return 0;\\n}' not found in stereo_euroc.cc"
    open(f, "w").write(s2)
    print("patched stereo_euroc.cc with per-frame tracking-time print")

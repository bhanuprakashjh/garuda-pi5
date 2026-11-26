# Chennai camera kit · operator field guide

Use this guide at the printer, on the bench and again after mounting the camera on the airframe. The first deliverable is a ZIP that lets another engineer match every tape reading to its RGB image, depth image and printed target. Allow a second person to move the board or read the tape while the operator captures. Assign one person to keep the placement log.

## 1. Receive the kit and identify the station

- Record the OAK-D Pro serial or asset label, Pi/host ID, USB cable, mount, cooling, date, operator and software revision. Photograph the assembly, tape zero and target backing. Give the configuration a short ID such as `CHN-bench-01`. Use a different ID after mounting on the aircraft.
- Chennai already runs the CM3 camera and Hailo detector on its Pi. Start there. With the existing demo open, save a screenshot of the CM3 source and confirm fresh detections. Ask the engineer to record the working CM3 profile so the team can return to it.
- Connect the OAK-D Pro over USB 3. The CM3 can stay attached. Have the engineer select the OAK profile for this bench session. The demo uses one camera at a time. In OAK mode its RGB image feeds the Hailo detector and its left/right images feed the Pi's stereo computation. The depth map lines up with the OAK RGB view. Continue using the CM3 profile for CM3 trials.
- With the fan in its intended position, open `http://<Pi-IP>:8098/demo` from a computer on the bench LAN. Click **Calibration → Open guided camera calibration**, or go directly to `http://<Pi-IP>:8099/camera`. Use the Pi's address. Port 8099 on the simulator laptop serves a different application.
- Confirm a moving RGB picture, matching depth panel, advancing frame number and the expected depth backend. The OAK supplies images and optional H.265. This release computes stereo on the Pi. Black pixels in the depth image have no reading. Save a screenshot of the status before capture.
- Take a **Practice** session if the board, tape or depth source is uncertain. Record the reader, camera and board labels now so another operator can repeat the setup.

## 2. Print and verify the target

Open **Print an A4 target** in the camera workspace. Print one page of `oak-targets-a4.pdf` on matte paper, A4 landscape, **Actual size / 100%**. Disable fit, shrink, borderless enlargement and duplex. Keep the complete white border. Start with sheet 1. Choose the matching **Printed sheet** in the GUI to fill in the counts. Measure and enter the pitch yourself.

| Sheet | Select in the GUI | Columns × rows to enter | Nominal pitch |
| --- | --- | --- | --- |
| 1 | Checkerboard | 9 × 6 inner corners (10 × 7 squares) | 20 mm square side |
| 2 | Checkerboard | 11 × 8 inner corners (12 × 9 squares) | 15 mm square side |
| 3 | Circle grid | 7 × 5 centres | 20 mm adjacent centre spacing |
| 4 | Offset circles | 4 centres in each of 7 staggered rows | 15 mm perpendicular row spacing, 30 mm between same-row neighbours |

1. Measure both 100 mm bars between tick centres with a ruler. Write down both readings. If horizontal and vertical scaling differ beyond your ability to measure, correct the printer settings and reprint.
2. Measure pitch on the actual sheet. For sheet 1, measure between the first and ninth inner corner on a row and divide by **8**. Cross-check top to bottom over **5** intervals. For sheet 4, measure from the first row to the seventh row perpendicular to the rows and divide by **6**. Enter the measured pitch in **mm**. Keep the sheet ID, two bar readings, measured spans and ruler resolution with the run.
3. Fasten the whole sheet to a rigid, matte backing. Check across both diagonals and along the edges with a straightedge. Look for light gaps, lifted corners and ripples. Record how you checked and any visible gap. Remount a bowed sheet and check again. Select **Mounted and checked flat** only after checking.
4. Place the target under diffuse light with the whole border visible. Keep hands and tape outside the pattern. Move closer or use a larger, measured chart if the far pattern cannot be detected. Use a fresh session when changing boards, counts or pitch.

## 3. Establish a repeatable tape reference

Keep the camera stationary. Mark a point or plane on the rigid housing or bracket that the tape hook can find again. Mark the **board front face at its centre** in your setup photo. Preserve the pattern itself. Enter a description such as “front face of mounting plate at lens height to printed face at board centre.” Draw or photograph both endpoints if another operator could interpret this differently.

Face the board squarely toward the camera, centred at lens height. Run the tape straight, parallel to the viewing direction. A slant to a side or down to the floor measures a different length. If the housing obstructs the tape, transfer both endpoints to a parallel measurement line with a straightedge and document how. Use consistent hook contact, light tension and eye-level readings. Repeat the measurement after repositioning the tape. Record the tape graduation, repeated readings and uncertainty estimate. Leave uncertainty blank if unknown.

**Units matter.** A reading of 126 cm enters the distance field as 1.26 m. The pitch and optional tape uncertainty fields use millimetres. This example is for conversion only. Every saved placement needs its own reading.

## 4. Capture and check board depth

1. Choose **Depth at measured distances**. Enter pattern, counts, measured pitch, flatness, operator, tape reference, camera ID and board ID. Choose **Bench** or **Grounded airframe** and enter the mount ID or revision. Use a descriptive session name. Start the session.
2. Choose near, middle and far positions within the range your team intends to test. Write down the intended range and your application tolerance before looking at results. Choose positions where the full board fits and detection works. Record actual distance at each placement.
3. At each **Setup / tuning** position, hold still, read the tape, enter metres, tick the measured-placement box, and enter uncertainty if known. In Notes record position, lighting and anything unusual. Click **Capture RGB + depth**. In **Inspect your captures**, click the view and confirm that the detected dots land on the intended pattern. Inspect depth coverage inside the board.
4. Keep misses. If pattern detection fails, retain that view, correct framing, glare or pattern counts, and capture again with a new measurement. If capture itself errors before saving, log the attempted position and error separately. A blank or black area carries no distance.
5. After three usable setup views, use **Independent validation**. Remove the board from position and set it up again. Measure three fresh placements spanning the useful range. A second operator or a separate session is useful. Preserve the setup/validation labels. Use new validation recordings after adjusting an estimator.
6. Click **Download images & report ZIP**. Open it on a second computer. Check `session.json`, `board-tape.csv`, and at least one RGB PNG, raw depth PNG and preview for the same capture ID. Save the original ZIP under its session ID. Keep the 16-bit depth PNG intact. A screenshot or coloured JPEG loses the raw millimetre values.

### Read the result

In `board-tape.csv`, filter by `split=validate` and count **all saved rows** first. A row with `comparison_status=raw_reference_difference` has a measured reference, declared-flat board, detected pattern and valid board median. The signed column is **board median minus tape reading**, in metres. A positive value places the reported board farther away than the marked tape reference. For a 1.20 m tape reading and 1.23 m median the raw difference would be +0.03 m. This arithmetic alone cannot establish the optical origin or an accuracy specification.

The ZIP also contains `board-tape-summary.json` with separate setup and validation counts, median signed and absolute differences and the largest absolute difference. Report each distance, signed difference, uncertainty and valid-pixel fraction. Give the number eligible over total validation captures. List missing-pattern, missing-depth, unconfirmed-tape and flatness cases separately. A single small depth patch can yield a plausible median. Review the overlay and compare coverage across distances. Historical entries without an explicit measured confirmation stay unconfirmed, including the earlier guessed 0.4 m. Keep elementary tape uncertainty separate from stereo repeatability.

## 5. Other calibration tasks

- **RGB lens model.** Start a separate **RGB lens model · advanced** session. Capture at least 12 detected setup views at different distances, positions and tilts, covering the centre, sides and corners. Add three separately positioned validation views. Run **Estimate RGB intrinsics candidate** and export the ZIP. Inspect validation reprojection errors in pixels and record the delivered image size and mode. With host stereo this fits already-undistorted RGB. Factory stereo calibration, optical-axis verification and camera-to-IMU calibration each require separate evidence.
- **Selected-person distance.** The board median and the demo's torso range sample different regions. With a consenting volunteer on the stationary bench, document the chosen torso reference plane, pose and tape method. Record the live reading, its source, missing samples and independent placements. Keep these records separate from the board ZIP.
- **Detection, gestures, mono and tracking observations.** Open the calibration centre and select the corresponding check type. Use the matching camera profile, record the model/source shown, expected and observed behaviour, and missed or false responses. For a mono estimate, record measured tape separately. Download the operator JSON **before** closing the tab. Those manual records live in the browser until downloaded.

## 6. Repeat on the mounted camera

Start with the aircraft restrained and disarmed, propellers removed and the camera powered in the intended configuration. Record the mount orientation, cable routing, lens clearance, vibration isolators, power source and cooling. Verify the mount stays rigid and the USB cable cannot pull the camera out of alignment. Photograph the as-installed housing datum and board reference.

Open a **new session** tagged with the airframe and mount revision. Repeat the independent near/middle/far board checks in the mounted configuration. Check the frame edges for obstructions, glare from the airframe, intermittent USB or stale depth, and a change in coverage or raw differences. Record any mount shifts or reboots. Recheck the affected placements after a mechanical change. Report these as grounded mounted-camera observations. Airborne testing and flight acceptance belong to the team's separately approved flight-test process.

## 7. Troubleshooting and return package

| Symptom | What to check and record |
| --- | --- |
| No moving RGB or depth | Confirm host/IP and active OAK profile, USB 3 data connection, depth backend and advancing frame. Save the status. Have the engineer inspect the single camera owner. |
| Depth present, pattern missing | Check sheet number, exact point counts, glare, bow and full border. Try a closer position before changing software. |
| Camera run changed | Export the old session. Choose **New depth session with these details**, recheck the physical setup, then capture again. |
| Differences vary by distance or image position | Keep the raw images, pitch, board alignment, coverage and reference photos. Repeat independently before proposing a correction. |
| ZIP export or storage fails | Retain the session ID and error. Default workspace allows 20 sessions, 60 captures per session and 1 GiB. Verify existing exported copies before archiving a session. |

Give the receiving engineer the session ZIPs, optional manual JSON, setup and mount photos, manifest below, and one short issue per reproducible failure. Keep recordings with people in the team's approved private evidence store. Use a digest to identify the exact file, for example `sha256sum garuda-calibration-*.zip` on Linux.

| Return manifest | Fill in at Chennai |
| --- | --- |
| Operator, date, camera serial, host and Git revision | |
| Board ID, print page, measured pitch, horizontal/vertical scale, flatness check | |
| Tape zero, endpoint, graduation, uncertainty and units | |
| Bench/mount ID, lighting, cooling and photo references | |
| Setup and independent session/capture IDs, ZIP names and SHA-256 | |
| Per-distance raw differences, eligible/total, failed attempts and coverage | |
| Changes between sets, open questions, reviewer and next owner | |

The receiving engineer can use the camera receipts, tape comparisons and screenshots to reconcile camera behaviour with the local tracking branch.

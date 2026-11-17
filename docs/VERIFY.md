# Verifying it, and what I could not test

## 1. Is the sky correct? (compare with Stellarium)

1. Open https://stellarium-web.org. Set the location to **Ottawa** (lat
   45.4 N, lon 75.7 W), the time to one of the rows below, and the
   **atmosphere on** (the default).
2. Click each object and read **Az/Alt** (azimuth from north through east,
   then altitude).
3. They should match this table within 1° (in practice expect ±0.1°). These
   values come from the renderer itself (`kindlesky.astro.compute`).

| Local time (Ottawa) | Polaris alt / az | Low southern star alt / az | Saturn alt / az |
|---|---|---|---|
| Wed Nov 25 2026, 8:00 PM EST | 45.9° / 0.6° | Fomalhaut 13.1° / 197.4° | 45.3° / 174.0° |
| Thu Dec 10 2026, 7:30 PM EST | 45.9° / 0.5° | Fomalhaut 11.3° / 203.6° | 45.3° / 184.6° |
| Wed Jan 20 2027, 10:00 PM EST | 45.9° / 359.4° | Sirius 26.9° / 167.1° | 8.2° / 263.9° |
| Mon Feb 15 2027, 9:00 PM EST | 45.8° / 359.3° | Sirius 27.9° / 178.4° | 3.1° / 270.7° |

To check any other time, render that moment yourself and open the images:

```bash
python -m kindlesky.render --out /tmp/check --hours 0 --at 2026-12-10T19:30-05:00
```

**On the image, check:**

- **Planets:** the same planets are above the horizon as in Stellarium, each
  with a label. Planets you can't see are listed at the bottom ("Below
  horizon: …", "Out of view: …").
- **South view:** Saturn sits near the centre line (the S tick) when its
  azimuth is about 180°. Fomalhaut is low and a little right of S (west of
  south).
- **Overhead view:** Polaris is about halfway between the centre and the N
  edge (its altitude ≈ your latitude). East is on the **left**: that is
  correct for a chart held over your head.
- **Constellation lines** end on stars.

**What was checked automatically:** positions agree with astropy (an
independent implementation) to within 0.01° for objects above the horizon.
Pixel positions round-trip back to the right altitude/azimuth within 0.2°.
I could not run Stellarium here, so the table above has **not** been
compared with Stellarium by me. That comparison is yours to do.

## 2. Are the scheduled updates running?

- **Website:** open `https://<you>.github.io/kindle-sky/`. "Rendered …"
  should be less than ~40 minutes old (runs are at :11 and :41, and GitHub
  often delays them). If it is more than ~2 hours old, updates are not
  arriving. Also look at
  `https://<you>.github.io/kindle-sky/status.json`: `rendered_at` is in UTC
  and `last_slot` is the last frame time (Unix seconds).
- **Kindle:** the header shows **"Sky at …"** (should be within 5 minutes of
  the time on your watch) and **"rendered …"** (when GitHub made it). Within
  3 hours of the last successful run, "Sky at" keeps advancing even if runs
  are late. If "Sky at" stops changing, the Kindle can't download (check the
  log), or there have been no new frames for over ~3 hours.
- **GitHub:** Actions tab → **Render sky**. You should see a run roughly every
  30 minutes. A yellow banner saying the scheduled workflow was disabled
  means there were 60 days without activity; click **Enable workflow**.
- **Kindle log:** `kindle-sky/kindle-sky.log` on the Kindle drive. Lines like
  `download failed: south (curl exit 6)` mean no network. `exit 22` means the
  frame wasn't on the site (404). `exit 60` is a certificate problem (see
  SETUP). `rejected download` means the file wasn't a valid image.

## 3. What I could NOT test or verify

These need checking by hand on your Kindle. All the Kindle logic is tested
on Linux against fake versions of the Kindle commands. The fakes encode my
assumptions, and the assumptions below are exactly what the fakes can't
prove.

| # | Assumption | How you'll notice if it's wrong |
|---|---|---|
| 1 | `stop lab126_gui` / `start lab126_gui` stop and restore the UI on your firmware (true for FW 5.x per KOReader's source). I don't know your exact firmware version. | Sky mode starts but the library stays on screen, or the UI doesn't come back after EXIT (then hold power ~40 s). |
| 2 | Stopping the UI doesn't also kill sky mode. It runs in its own session (`setsid`) and ignores TERM while stopping the UI. | The screen goes black/blank and stays that way. The log ends after "sky mode starting". |
| 3 | The touchscreen is `/dev/input/event1` (KOReader's PW3 profile), sends 16-byte events (32-bit kernel), and reports screen pixels in portrait without rotation. | Taps do nothing, or EXIT switches views. See "Touch calibration" in SETUP. |
| 4 | The finger lifting is reported as `ABS_MT_TRACKING_ID = -1` or `BTN_TOUCH = 0` (either works). | Taps never register. |
| 5 | The Kindle's busybox has `od -b`, `dd`, `awk`, `sed`, `cut`, `tr`, and `setsid` or `nohup`. Tested with busybox 1.36; your Kindle's busybox is likely older. | Sky mode refuses to start and names the missing command in the log. |
| 6 | `fbink -q -f -g file=…` (or `eips -f -g`) draws an 8-bit grayscale PNG full-screen. | The image doesn't appear, or appears shifted or garbled. |
| 7 | The Kindle's `curl` supports `--proto`, `--max-filesize` and TLS 1.2, and its CA store trusts github.io's certificate. | `curl exit 1/2` (unknown option) or `exit 60` (certificate) in the log. |
| 8 | Wi-Fi stays connected while the UI is stopped, and `preventScreenSaver` keeps the Kindle awake. | Downloads fail after a while (`curl exit 6/7`), or the screen goes to sleep. |
| 9 | `lipc-wait-event com.lab126.powerd goingToScreenSaver,outOfScreenSaver` reports a power-button press while the UI is stopped. | The power button doesn't exit sky mode. EXIT, triple tap and the timeout still work. |
| 10 | SH_Integration runs `Sky Mode.sh` as root via `sh` (from its source code, v4.1.0); I don't know your installed version. | Tapping the tile does nothing. Check `kindle-sky.log` for a "launched" line. |
| 11 | Restarting the UI takes about as long as a normal UI restart. Not measured. | — |
| 12 | The GitHub workflows run on GitHub. actionlint passes and every install command was run locally, but nothing ran on GitHub's servers. That includes the pairing of `upload-pages-artifact` v5 with `deploy-pages` v5, and `go` being on the runner. | The first **Render sky** or **CI** run fails. Send me the log. |
| 13 | Battery life in sky mode. Not measured; Wi-Fi stays on. | — |

## 4. Test, reliability and mutation results (run in this session)

- **Renderer tests** (`tests/test_render.py`, `tests/test_consistency.py`):
  image size, mode and grey levels; header, footer and EXIT box; ground and
  horizon; planets drawn at their computed pixel and labelled; every visible
  constellation-line star drawn; positions against astropy reference values;
  a failing layer, planet or frame isolated; the validator; the CLI; shared
  constants between the renderer and `skyd.sh`.
- **Kindle tests** (`tests/test_kindle.py`, each under dash and busybox ash
  with busybox's `od`/`dd`/`awk`):
  - **Lifecycle:** start, stop, EXIT, triple tap, power button, view
    switching (including a tap mid-download), the refresh schedule.
  - **Downloads:** failure with retry, 404, and rejected files (HTML, RGB,
    wrong size, 16-bit, interlaced, too big, truncated, empty).
  - **Process safety:** stale or reused PID files, bystander processes,
    crashed helpers, `MAX_RUNTIME`.
  - **Touch:** calibration settings, 64-bit events.
  - **Also:** the CA-bundle option, log trimming, the scriptlet header.
- **Reliability (all in this session):**
  - **Renders:** 500 renders at random dates from 2000 to 2052, both views,
    with 0 drawing errors (`pytest -m slow`). Each was checked so that every
    planet above the horizon in view is labelled.
  - **Full sites:** 10 complete 3-hour site renders (760 images). All passed
    `kindlesky.validate`, each taking ~12 s.
  - **Kindle suite:** run 6+ times; the final versions all pass under both
    dash and busybox ash.
  - **Flaky tests found along the way:** two timing tests, caused by the
    100× fake clock amplifying process start-up latency on a loaded machine.
    Their bounds were widened to the property that matters (a retry comes
    before the next slot). The script itself was not changed for this.
- **Mutation testing, Kindle script** (`python tools/mutate_shell.py`):
  - **Result:** 227 mutants; 226 caught, 1 survived.
  - **First pass:** 210/227 caught. The 17 survivors exposed missing tests,
    now added: exact EXIT-box corner, exact 1,000/999 and
    2,000,000/2,000,001-byte limits, a decoy process with our command line
    but the wrong start time, and "no process sky mode started is left
    running after exit". Re-running every mutant on those lines against the
    final tests caught all of them except one.
  - **Survivor:** `2>/dev/null` → `3>/dev/null` inside the power-watcher's
    TERM trap. It is *equivalent*: it only changes where an error message
    from `kill` is discarded, so no test can catch it.
  - **Not mutated, by design:** the user-settings block, because `config.sh`
    overrides it in every test.
- **Mutation testing, renderer:** **not run.** `mutmut` is configured in
  `pyproject.toml` (run `mutmut run`, then `mutmut results`), but I skipped
  the long run to save time. The renderer tests check concrete pixel
  positions, reference coordinates and failure isolation, but how many
  mutants they would miss is unmeasured.

# Design choices and trade-offs

## Overview

```
GitHub Actions (twice an hour)                    GitHub Pages                   Kindle (sky mode)
render.py ─► frames for now … +3 h, every 5 min ─► frames/south-<epoch>.png ─►  every 5 min: fetch the frame
             validate.py checks every image        frames/overhead-<epoch>.png   for its own clock, check it,
                                                   status.json, index.html       draw it full-screen
```

### Pre-rendered frames instead of "the latest image"

GitHub's scheduled workflows are best-effort. Runs are often 10–30 minutes
late, and some are skipped. With a single "latest" image, your 15-minute
freshness limit would depend on GitHub. Instead, each run renders a frame
every 5 minutes for the next 3 hours (≈ 76 images, ~12 s of rendering). The
Kindle requests `frames/<view>-<slot>.png`, where `<slot>` is its own clock
rounded down to 5 minutes. The sky it shows is therefore at most ~5 minutes
behind reality, as long as at least one run gets through every ~2.5 hours.

**Trade-off:** this relies on the Kindle's clock. Kindles set it from the
network, but if it is wrong, the sky is wrong by the same amount. The header
shows "Sky at …" for the frame and "rendered …" for the run, so both are
visible.

### Renderer: Skyfield + Pillow, written for e-ink (replaces starplot)

The previous version used starplot. I replaced it because:

- **Unpinned downloads:** starplot downloads star data and a DuckDB extension
  (native code) at runtime from the internet. These are not covered by
  `requirements.txt` hashes, which breaks your rule that a compromised upstream
  must not silently change what is published.
- **Instability:** it crashed about 1 render in 8 (the Jupiter NaN the old
  code worked around).
- **Too heavy:** about 40 dependencies (astropy, duckdb, matplotlib, shapely, …)
  for what is a fairly small drawing job.

The new renderer has five direct dependencies, all installed as hash-pinned
wheels: skyfield, skyfield-data (the JPL DE421 ephemeris), numpy, Pillow and
tzdata. The star and constellation data are committed to the repo (see
`kindlesky/data/SOURCES.md`). Nothing is downloaded at render time, so the
same commit always produces the same pixels.

**Accuracy:** Skyfield computes apparent topocentric positions (precession,
nutation, aberration, light-time) plus standard atmospheric refraction (10 °C,
1010 mbar), as Stellarium does by default. Checked against astropy, an
independent implementation: within 0.01° above the horizon (tests pin this).

**Projection:** stereographic, also Stellarium's default. In the south view
the projection is centred on the south point of the horizon, which makes the
horizon a perfectly straight line along the bottom. Constellation shapes stay
undistorted, and the zenith sits at the top. The view spans about SE (126°)
to SW (234°) along the horizon. The overhead view is a standard star chart:
zenith in the centre, north up, east on the left.

**E-ink choices:**

- **Colours:** black sky and white stars, quantised to the panel's 16 grey
  levels.
- **Rendering:** drawn at 2× and downsampled for smooth edges.
- **Planets:** labelled in 40 px bold, which is about 3.4 mm tall on the
  300 ppi screen.
- **Sizes:** stars are sized by magnitude.
- **Lines:** constellation lines stop just short of each star, so they always
  visibly end on one.

Planets are placed first and their labels are never dropped. A note at the
bottom lists naked-eye planets that are below the horizon or outside the
view. Uranus and Neptune are never drawn.

**Failure isolation:** each layer (grid, lines, stars, each planet, labels, …)
is drawn inside its own try/except. A failing object is logged in
`status.json` and the rest of the image is still produced. A failing frame
doesn't stop the other frames. The run only fails if the current frames are
missing.

## Kindle side

**Stopping the Kindle UI (your decision):** while sky mode runs,
`stop lab126_gui` stops the Kindle's UI service through Upstart, and
`start lab126_gui` brings it back. KOReader's "no framework" mode does the
same. It is the only reliable way to keep the clock, status bar and library
from drawing over the sky, and to stop taps reaching invisible library tiles.
It does affect system processes, which you accepted explicitly. Everything
else follows "only touch what we started":

- **Signals:** the script only signals PIDs it recorded itself, and checks the
  process start time and command line from `/proc` before signalling. A stale
  or reused PID in the PID file is never killed (tested).
- **On exit:** the same `cleanup` path always runs, whether the exit comes
  from the EXIT tap, a triple tap, the power button, a crashed helper, an
  unreadable touchscreen, the 12-hour limit or `skyd.sh stop`. It stops sky
  mode's own helpers, waits for them, restarts the UI and re-allows sleep.
- **Preflight checks:** before stopping anything, the script checks that
  every tool it needs exists and that the touchscreen is readable. If not, it
  shows a message and leaves the Kindle alone.
- **Upstart's TERM:** when it stops the UI, Upstart sends TERM to the
  processes it stops. The script ignores TERM for that moment (as KOReader
  does) and runs in its own session (`setsid`).

**Touch input:** the script reads raw `input_event` records from
`/dev/input/event1` with `dd`, decodes them with `od` and `awk`, and acts when
the finger lifts. It needs no extra binaries, which also means nothing new
running as root. The cost is that the event layout and coordinate range are
assumptions from KOReader's PW3 profile, not something I could test on
hardware. Hence the calibration settings and the tap log.

**Downloads are checked before drawing:**

- **HTTPS only:** `--proto =https`, no redirects followed, never `-k`,
  `--max-filesize 2000000`, timeouts set.
- **Image checks:** the file must be 1000 bytes to 2 MB, with a PNG signature
  and an IHDR chunk saying exactly 1072 × 1448, 8-bit, grayscale,
  non-interlaced.
- **Failure:** the old image stays, and the script retries after 60 s.

**What this does not protect against:** the check is a sanity check, not a
signature. Anyone who controls your GitHub repo, or GitHub Pages itself, can
publish a well-formed PNG, and it will be decoded by fbink/eips as root. Real
protection would need a signing key, which means a secret in CI, which you
ruled out. The trust anchor is therefore your GitHub account: use 2FA.

## CI security (least privilege, pinning)

- **Token:** `permissions: {}` at the top of each workflow. Each job asks for
  the minimum.
- **Render job:** runs the third-party Python with `contents: read` only, and
  `persist-credentials: false`, so there is no token on disk.
- **Deploy job:** the only job with `pages: write` and `id-token: write`. It
  runs only GitHub's `deploy-pages` action, with no repository code and no
  pip packages.
- **Action pins:** every action is pinned to a full commit SHA (checked
  against the release tags with `git ls-remote`). `upload-pages-artifact`
  itself pins `upload-artifact` by SHA.
- **Python pins:** `pip install --require-hashes --only-binary :all: --no-deps`.
  Every package and its exact file hash is locked for both x86-64 and ARM64.
  Only wheels are installed, so no package build scripts run, and nothing
  unlisted can be pulled in.
- **actionlint:** in CI it comes from `go install …@v1.7.12`, verified against
  Go's checksum database. The PyPI wrapper downloads an unpinned binary at
  install time, so I didn't use it.
- **Pages concurrency:** `concurrency: pages` with
  `cancel-in-progress: false`, so a deploy is never cut off half-way.

## Privacy

**Location:** 45.4 N, 75.7 W, rounded to one decimal place (~10 km), which is
city level. The images say "Ottawa", nothing more precise.

**Things in the existing repo and its history that identify you:**

1. **The old location** was `LAT, LON = 45.30, -75.90   # Kanata, Ottawa`.
   That is a specific suburb, more precise than city level. It is in
   `render.py` in commits `0216e92` through `7523876`, and in the old test file.
   It is gone from the new files, but **not from git history**.
2. **Your full name:** every existing commit's author name is your full name.
   The author email is your GitHub noreply address, which is fine.
3. **Your GitHub username** is in `BASE_URL`, in the Pages URL and in the
   README. That can't be avoided with GitHub Pages short of a custom domain.

**How to avoid them:**

- Publish from a fresh history. Start from these files without `.git` (the
  tarball), and set `user.name` to your handle before the first commit (see
  SETUP.md).
- If the old repo is or was public, assume the old commits have been seen.
  Rewriting history doesn't remove commits GitHub has already cached (forks,
  PR refs). Deleting that repo removes them; GitHub Support can purge cached
  views.
- Keep "Block command line pushes that expose my email" on.
- The Kindle log (`kindle-sky.log`) contains only times, tap coordinates and
  URLs, and it stays on the Kindle.
- The public Actions logs show render times and errors only.

# kindle-sky

A window to the night sky over Ottawa for a jailbroken Kindle Paperwhite 3
(1072 × 1448, e-ink). It is a drawing of the real current sky (stars,
constellations, the naked-eye planets and the Moon), so it works at any time of
day.

- **Two views:** *Looking south* (horizon along the bottom) and *Overhead*
  (the whole sky as a circle).
- **Sky Mode:** tap the **Sky Mode** tile in the Kindle library to start it.
  Tap the sky to switch views. Tap **EXIT** to get the normal Kindle back.
- **Always current:** GitHub Actions renders a frame every 5 minutes for the
  next 3 hours and publishes them on GitHub Pages. The Kindle shows the frame
  for "now", so the sky is at most ~5 minutes old even when scheduled runs are
  late.
- **Accurate:** positions come from JPL's DE421 ephemeris via Skyfield,
  checked against an independent implementation to within 0.01°.

Preview: https://shecodesmagic.github.io/kindle-sky/

| | |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | GitHub Pages settings, first run, copying files to the Kindle, touch calibration |
| [docs/DESIGN.md](docs/DESIGN.md) | Design choices, trade-offs, security model, privacy notes |
| [docs/VERIFY.md](docs/VERIFY.md) | Checking against Stellarium, checking that updates run, what is untested on real hardware |

## Layout

```
kindlesky/              renderer (Python ≥ 3.12): astro.py, projection.py, draw.py, render.py, validate.py
kindlesky/data/         vendored star & constellation data (pinned, see SOURCES.md)
kindle/kindle-sky/      goes to /mnt/us/kindle-sky on the Kindle: skyd.sh, config.sh
kindle/documents/       goes to /mnt/us/documents: "Sky Mode.sh" (the library tile)
tools/                  catalogue builder, shell mutation tester
tests/                  renderer tests, Kindle-script tests on a fake Kindle
.github/workflows/      render.yml (render + deploy), ci.yml (lint + tests)
```

## Quick start (local)

```bash
pip install --require-hashes --only-binary :all: --no-deps -r requirements-dev.txt
```

```bash
python -m kindlesky.render --out public && python -m kindlesky.validate public
```

```bash
pytest -q
```

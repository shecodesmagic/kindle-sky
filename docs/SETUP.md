# Setup

Three parts: (1) a public GitHub repo that renders and publishes the images,
(2) the files on the Kindle, (3) a first run. Allow 20–30 minutes.

## 1. GitHub repo and Pages

### Before your first commit: author identity

Git records an author name and email in every commit, and in a public repo
everyone can read them. Do this once, inside the repo folder:

```bash
git config user.name "shecodesmagic"
```

```bash
git config user.email "ID+shecodesmagic@users.noreply.github.com"
```

Use your own noreply address. It is shown at github.com → Settings → Emails,
under "Keep my email addresses private". Turn on **Block command line pushes
that expose my email** on that page too.

### Create the repo and push

1. On GitHub, create a new **public** repository (e.g. `kindle-sky`), with no
   README, licence or .gitignore, so the first push isn't rejected.
2. Start from a folder **without** old git history: the `kindle-sky.tar.gz`
   I sent you, unpacked, or a copy of these files without `.git`. In that
   folder, commit everything and push to `main`:

```bash
git init -b main
```

```bash
git add -A && git commit -m "Kindle sky"
```

```bash
git remote add origin https://github.com/shecodesmagic/kindle-sky.git
```

```bash
git push -u origin main
```

If you reuse the existing `kindle-sky` repo instead, read "Privacy" in
[DESIGN.md](DESIGN.md) first: its history contains a more precise location and
your full name.

### Settings (github.com → your repo → Settings)

| Where | Set to | Why |
|---|---|---|
| **Pages** → Build and deployment → Source | **GitHub Actions** | The workflow deploys; no `gh-pages` branch. |
| **Actions → General** → Actions permissions | **Allow *your-account*, and select non-*your-account*, actions and reusable workflows** → tick only **Allow actions created by GitHub** | The workflows only use `actions/*`; nothing else can be added silently. |
| **Actions → General** → (same section) | Tick **Require actions to be pinned to a full-length commit SHA**, if your account shows it | Enforces what the workflows already do. |
| **Actions → General** → Workflow permissions | **Read repository contents and packages permissions**; leave "Allow GitHub Actions to create and approve pull requests" **off** | The default token is read-only. The deploy job asks for `pages: write` itself. |
| **Environments** → `github-pages` (appears after the first run) | Deployment branches: **main** only (the default) | Only `main` can publish. |

No secrets are needed. Don't add any.

### First run

1. **Actions** tab → **Render sky** → **Run workflow** (branch `main`).
2. Wait for both jobs (`build`, then `deploy`) to go green. The first run
   takes ~2 minutes.
3. Open `https://shecodesmagic.github.io/kindle-sky/`. You should see both
   images and a "Rendered …" time a few minutes old.
4. The **CI** workflow (tests and lint) runs on every push. It should be
   green too.

After that, **Render sky** runs by itself twice an hour (at :11 and :41). Each
run publishes frames for the next 3 hours, so late or skipped runs don't
matter as long as one gets through every ~2.5 hours.

> **Scheduled runs stop after 60 days without repo activity.** GitHub disables
> schedules in public repos when nothing has been pushed for 60 days and
> emails you a warning first. To restart: Actions → Render sky → **Enable
> workflow**. Any push also resets the 60 days.

## 2. Copy files to the Kindle

The Kindle must already have the WinterBreak jailbreak with the Universal
Hotfix, which provides scriptlet support (SH_Integration) and `fbink`.

1. Connect the Kindle to the Mac by USB. It mounts as `/Volumes/Kindle`.
2. Copy the folder `kindle/kindle-sky` to the root of the Kindle, so
   `/Volumes/Kindle/kindle-sky/skyd.sh` exists.
3. Copy `kindle/documents/Sky Mode.sh` into `/Volumes/Kindle/documents/`.
4. If your GitHub username or repo name differ, edit `BASE_URL` in
   `/Volumes/Kindle/kindle-sky/config.sh`.
5. macOS leaves hidden `._*` files on the Kindle's FAT drive. SH_Integration
   may list `._Sky Mode.sh` as a second, broken tile. Remove them before
   ejecting:

```bash
dot_clean -m /Volumes/Kindle
```

6. Eject the Kindle in Finder and unplug it.

A "Sky Mode" tile should appear in the library after a few seconds. If it
doesn't, restart the Kindle.

## 3. Use

- Turn **Wi-Fi on**, then tap **Sky Mode**. The screen goes blank briefly while
  the Kindle's UI stops, then the sky appears (or "Loading the sky..." for a
  few seconds the first time).
- **Tap anywhere on the sky**, or the left of the bottom bar, to switch
  between *Looking south* and *Overhead*.
- **Tap EXIT** (bottom right) to return to the normal Kindle. The UI restarts,
  which takes about as long as after a reboot of the UI (~20–40 s, not
  measured on your device).
- Fallbacks if EXIT doesn't respond: **tap three times within 2 seconds**,
  or press the **power button**. Sky mode also hands the Kindle back by itself
  after 12 hours (`MAX_RUNTIME` in `config.sh`).
- Last resort: **hold the power button ~40 s** to restart the Kindle.
  Nothing from sky mode survives a restart.
- While sky mode runs, the Kindle doesn't go to sleep, so the battery drains
  faster. Plug it in for long sessions.

### Touch calibration (only if taps do the wrong thing)

Every tap is logged in `kindle-sky/kindle-sky.log` on the Kindle drive:

```
2026-11-20 21:04:10 tap raw=(905,1398) screen=(905,1398)
```

`screen=` should match where you tapped (0,0 is the top-left corner; the
screen is 1072 × 1448). If it doesn't, tap the four corners, then read the log
over USB:

| What you see | Set in `config.sh` |
|---|---|
| x and y are swapped | `TOUCH_SWAP_XY=1` |
| left/right is reversed | `TOUCH_MIRROR_X=1` |
| top/bottom is reversed | `TOUCH_MIRROR_Y=1` |
| raw numbers go far beyond 1072 / 1448 | set `TOUCH_MAX_X` / `TOUCH_MAX_Y` to the largest raw values you saw |
| no `tap` lines at all | wrong device; try `TOUCH_DEV=/dev/input/event0` or `event2` |

### If the log shows `curl exit 60`

The Kindle's built-in certificate store couldn't verify github.io. Download
Mozilla's CA bundle on the Mac, from https://curl.se/ca/cacert.pem. Copy it to
`/Volumes/Kindle/kindle-sky/cacert.pem`, and set this in `config.sh`:

```sh
CA_BUNDLE=/mnt/us/kindle-sky/cacert.pem
```

Never work around this by turning off verification.

## Running the renderer elsewhere (Mac, Raspberry Pi 5)

It needs Python ≥ 3.12. Raspberry Pi OS "Trixie" ships 3.13.

```bash
python3 -m venv .venv && . .venv/bin/activate
```

```bash
pip install --require-hashes --only-binary :all: --no-deps -r requirements.txt
```

```bash
python -m kindlesky.render --out public && python -m kindlesky.validate public
```

`requirements.txt` includes hashes for both x86-64 and ARM64 (aarch64)
wheels. On a Pi, serve `public/` over HTTPS with any web server and run the
render from cron every 30 minutes. Point `BASE_URL` at it; the Kindle accepts
only HTTPS with a certificate it can verify.

## Development

```bash
pip install --require-hashes --only-binary :all: --no-deps -r requirements-dev.txt
```

```bash
pytest -q
```

`pytest -m slow` runs the long stability test (500 random dates). Lint:
`ruff check .`, `shellcheck -s sh kindle/kindle-sky/*.sh`, `actionlint`.
Mutation testing: `python tools/mutate_shell.py` (Kindle script) and
`mutmut run` (renderer); see [VERIFY.md](VERIFY.md).

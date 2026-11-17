"""Render the published site: time-stamped frames for both views plus a status page.

    python -m kindlesky.render --out public

Writes:
    frames/<view>-<epoch>.png  one per STEP_MIN minutes, epoch = UTC seconds of the slot start
    <view>.png                 the frame for the current slot (for the web page / quick checks)
    status.json                when it was rendered, which slots exist, any drawing errors
    index.html                 a small status page
"""
import argparse
import html
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import astro, config, draw


def slots(now_epoch: int, step_min: int = config.STEP_MIN, hours: float = config.HOURS_AHEAD) -> list[int]:
    """Slot start times (UTC epoch seconds): one step before `now`, through `hours` ahead."""
    step = step_min * 60
    first = now_epoch // step * step - step
    last = now_epoch + int(hours * 3600)
    return list(range(first, last + 1, step))


def frame_name(view: str, epoch: int) -> str:
    return f"{view}-{epoch}.png"


def render_site(out: Path, now: datetime, step_min: int = config.STEP_MIN,
                hours: float = config.HOURS_AHEAD) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    frames = out / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir()
    now_epoch = int(now.timestamp())
    all_slots = slots(now_epoch, step_min, hours)
    current = now_epoch // (step_min * 60) * (step_min * 60)
    errors: list[str] = []
    done: list[int] = []
    for epoch in all_slots:
        when = datetime.fromtimestamp(epoch, UTC)
        try:
            sky = astro.compute(when)
            for view in draw.VIEWS:
                img, errs = draw.render_view(view, sky, now)
                img.save(frames / frame_name(view, epoch), optimize=True)
                errors += [f"{when:%H:%M}Z {e}" for e in dict.fromkeys(errs)]
            done.append(epoch)
        except Exception as e:  # noqa: BLE001 - keep the other frames
            errors.append(f"{when:%H:%M}Z frame failed: {type(e).__name__}: {e}")
    for view in draw.VIEWS:
        src = frames / frame_name(view, current)
        if src.exists():
            shutil.copyfile(src, out / f"{view}.png")
    status = {
        "rendered_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "place": config.PLACE, "lat": config.LAT, "lon": config.LON,
        "step_seconds": step_min * 60,
        "first_slot": done[0] if done else None,
        "last_slot": done[-1] if done else None,
        "slots": len(done),
        "current_slot": current,
        "views": list(draw.VIEWS),
        "errors": errors[:200],
    }
    (out / "status.json").write_text(json.dumps(status, indent=1) + "\n")
    (out / "index.html").write_text(index_html(status, now))
    return status


def index_html(status: dict, now: datetime) -> str:
    errs = "".join(f"<li>{html.escape(e)}</li>" for e in status["errors"][:20]) or "<li>none</li>"
    last = datetime.fromtimestamp(status["last_slot"], UTC) if status["last_slot"] else None
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kindle Sky</title>
<style>
body{{margin:0;padding:16px;background:#fff;color:#000;font-family:system-ui,sans-serif}}
img{{width:100%;max-width:536px;height:auto;display:block;margin:0 0 16px;border:1px solid #888}}
code{{font-size:90%}}
</style></head><body>
<h1>Kindle Sky: {html.escape(status['place'])}</h1>
<p>Rendered <b>{html.escape(draw.local_time(now))}</b> (<code>{html.escape(status['rendered_at'])}</code>).
Frames cover until {html.escape(draw.local_time(last)) if last else 'n/a'}.
If "Rendered" is more than an hour old, the scheduled workflow is not running.</p>
<p>Drawing errors: <ul>{errs}</ul></p>
<img src="south.png" alt="Sky looking south"><img src="overhead.png" alt="Sky overhead">
<p><a href="status.json">status.json</a></p>
</body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render Kindle sky frames.")
    ap.add_argument("--out", type=Path, default=Path("public"))
    ap.add_argument("--hours", type=float, default=config.HOURS_AHEAD)
    ap.add_argument("--step", type=int, default=config.STEP_MIN, help="minutes between frames")
    ap.add_argument("--at", help="render as if it were this ISO time (e.g. 2026-11-20T21:00-05:00)")
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.at) if args.at else datetime.now(UTC)
    if now.tzinfo is None:
        ap.error("--at needs a UTC offset")
    t0 = time.monotonic()
    status = render_site(args.out, now, args.step, args.hours)
    print(f"rendered {status['slots']} slots x {len(status['views'])} views in {time.monotonic() - t0:.1f}s; "
          f"{len(status['errors'])} drawing errors")
    for e in status["errors"]:
        print("  error:", e, file=sys.stderr)
    # Fail only if the frames needed right now are missing; partial errors still publish.
    return 0 if all((args.out / f"{v}.png").exists() for v in status["views"]) else 1


if __name__ == "__main__":
    sys.exit(main())

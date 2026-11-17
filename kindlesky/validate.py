"""Check a rendered site before it is published.

    python -m kindlesky.validate public

Applies the same rules the Kindle applies (PNG, 8-bit grayscale, 1072x1448,
size limit) plus content checks, so a bad render fails in CI instead of on the
device.
"""
import json
import struct
import sys
from pathlib import Path

from PIL import Image

from . import config

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def kindle_header_ok(data: bytes) -> bool:
    """Exactly what kindle-sky/skyd.sh checks: signature, IHDR, size, depth 8, grayscale, no interlace."""
    if len(data) < 33 or data[:8] != PNG_SIG or data[12:16] != b"IHDR":
        return False
    w, h, depth, color, _comp, _filt, interlace = struct.unpack(">IIBBBBB", data[16:29])
    return (w, h, depth, color, interlace) == (config.W, config.H, 8, 0, 0)


def check_png(path: Path) -> list[str]:
    problems = []
    data = path.read_bytes()
    if not 1000 <= len(data) <= config.MAX_PNG_BYTES:
        problems.append(f"{path.name}: size {len(data)} bytes outside 1000..{config.MAX_PNG_BYTES}")
    if not kindle_header_ok(data):
        problems.append(f"{path.name}: header is not an 8-bit grayscale {config.W}x{config.H} PNG")
        return problems
    with Image.open(path) as img:
        img.load()
        if img.mode != "L" or img.size != (config.W, config.H):
            problems.append(f"{path.name}: mode {img.mode} size {img.size}")
        hist = img.histogram()
    dark, bright = sum(hist[:40]), sum(hist[216:])
    if dark < config.W * config.H // 4:
        problems.append(f"{path.name}: sky is not dark ({dark} dark pixels)")
    if bright < 3000:
        problems.append(f"{path.name}: almost nothing drawn ({bright} bright pixels)")
    return problems


def check_site(out: Path) -> list[str]:
    problems = []
    try:
        status = json.loads((out / "status.json").read_text())
    except (OSError, ValueError) as e:
        return [f"status.json: {e}"]
    for view in status["views"]:
        p = out / f"{view}.png"
        problems += check_png(p) if p.exists() else [f"missing {p.name}"]
    step = status["step_seconds"]
    expected = list(range(status["first_slot"], status["last_slot"] + 1, step))
    if len(expected) != status["slots"]:
        problems.append(f"status.json says {status['slots']} slots but range has {len(expected)}")
    for epoch in expected:
        for view in status["views"]:
            p = out / "frames" / f"{view}-{epoch}.png"
            problems += check_png(p) if p.exists() else [f"missing frames/{p.name}"]
    return problems


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0] if argv else "public")
    problems = check_site(out)
    for p in problems:
        print("FAIL", p)
    print("ok" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

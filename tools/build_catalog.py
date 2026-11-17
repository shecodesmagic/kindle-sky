"""Rebuild kindlesky/data/ from d3-celestial (BSD-3-Clause), pinned by commit and SHA-256.

Usage:
    python tools/build_catalog.py            # download the pinned files
    python tools/build_catalog.py --src DIR  # use files already downloaded into DIR

Every input is checked against the SHA-256 below before use, so a changed or
tampered upstream file stops the build instead of silently changing the sky.
The outputs are committed to the repo; CI never downloads catalog data.
"""
import argparse
import csv
import hashlib
import json
import math
import sys
import urllib.request
from pathlib import Path

COMMIT = "7e720a3de062059d4c5400a379146a601d9010e0"
BASE = f"https://raw.githubusercontent.com/ofrohn/d3-celestial/{COMMIT}/"
FILES = {
    "data/stars.6.json": "0297b8fa3adfbce1dc26566f61c4abcc1df4f29c6a28729ca06b56d1c6d25602",
    "data/constellations.lines.json": "294f66bef5d5cf50b1e17f16d2efa1d97a15131612c68dd935adef6e7373e13c",
    "data/constellations.json": "ab4ae692027cbc042c0d6791a84456a65eb7c55656107fd00c58ff6e55d4d8b2",
    "data/starnames.json": "19c84bc885f8a97c3b8e1f6a380084c575a9758dedfe35256e911a823ec3a695",
    "LICENSE": "a8c79239001ad4bea243d1796b85084e1fc39309c5c5a663930a1b46320254c6",
}
OUT = Path(__file__).resolve().parents[1] / "kindlesky" / "data"

MAG_KEEP = 5.5        # keep every star at least this bright
NAME_MAG = 2.5        # keep proper names for stars at least this bright
SNAP_DEG = 0.05       # a line vertex must be this close to a catalogue star
# Mira (omicron Ceti, HIP 10826) is a long-period variable (mag ~2 to ~10) and is
# missing from stars.6.json, but Cetus' lines run through it. Add it at a
# nominal magnitude so the line ends on a drawn star.
EXTRA_STARS = [(10826, 34.8366, -2.9776, 4.0)]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(name: str, src: Path | None) -> bytes:
    if src is not None:
        data = (src / Path(name).name).read_bytes()
    else:
        with urllib.request.urlopen(BASE + name, timeout=60) as r:  # noqa: S310 - fixed https URL
            data = r.read()
    got = sha256(data)
    if got != FILES[name]:
        sys.exit(f"SHA-256 mismatch for {name}: got {got}, want {FILES[name]}")
    return data


def sep_deg(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def build(src: Path | None) -> None:
    raw = {name: json.loads(fetch(name, src)) for name in FILES if name.endswith(".json")}
    fetch("LICENSE", src)

    stars = {}
    for f in raw["data/stars.6.json"]["features"]:
        ra, dec = f["geometry"]["coordinates"]
        stars[int(f["id"])] = (ra % 360.0, dec, float(f["properties"]["mag"]))
    for hip, ra, dec, mag in EXTRA_STARS:
        stars.setdefault(hip, (ra, dec, mag))

    # Convert each line vertex to the HIP number of the star it sits on.
    by_cell = {}
    for hip, (ra, dec, _) in stars.items():
        by_cell.setdefault((round(dec), round(ra) % 360), []).append(hip)

    def nearest(ra, dec):
        best, best_d = None, 99.0
        for ddec in (-1, 0, 1):
            for dra in range(-3, 4):
                for hip in by_cell.get((round(dec) + ddec, (round(ra) + dra) % 360), ()):
                    d = sep_deg(ra, dec, stars[hip][0], stars[hip][1])
                    if d < best_d:
                        best, best_d = hip, d
        return best, best_d

    lines, line_stars = {}, set()
    for f in raw["data/constellations.lines.json"]["features"]:
        polys = []
        for poly in f["geometry"]["coordinates"]:
            hips = []
            for ra, dec in poly:
                hip, d = nearest(ra % 360.0, dec)
                if d > SNAP_DEG:
                    sys.exit(f"{f['id']}: line vertex {ra},{dec} is {d:.3f} deg from the nearest star")
                hips.append(hip)
            polys.append(hips)
            line_stars.update(hips)
        lines[f["id"]] = polys

    keep = sorted(h for h, (_, _, m) in stars.items() if m <= MAG_KEEP or h in line_stars)
    with open(OUT / "stars.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["hip", "ra_deg", "dec_deg", "mag"])
        for h in keep:
            ra, dec, mag = stars[h]
            w.writerow([h, f"{ra:.4f}", f"{dec:.4f}", f"{mag:.2f}"])

    (OUT / "lines.json").write_text(json.dumps(lines, sort_keys=True, separators=(",", ":")) + "\n")

    cons = []
    for f in raw["data/constellations.json"]["features"]:
        ra, dec = f["geometry"]["coordinates"]
        cons.append({"abbr": f["id"], "name": f["properties"]["name"], "ra": round(ra % 360.0, 3),
                     "dec": round(dec, 3), "rank": int(f["properties"]["rank"])})
    cons.sort(key=lambda c: c["abbr"])
    (OUT / "constellations.json").write_text(json.dumps(cons, ensure_ascii=False, indent=0) + "\n")

    names = {}
    for hip, v in raw["data/starnames.json"].items():
        h = int(hip)
        if v.get("name") and h in stars and stars[h][2] <= NAME_MAG:
            names[str(h)] = v["name"]
    (OUT / "starnames.json").write_text(json.dumps(dict(sorted(names.items(), key=lambda kv: int(kv[0]))),
                                                   ensure_ascii=False, indent=0) + "\n")
    print(f"stars={len(keep)} line_stars={len(line_stars)} constellations={len(cons)} names={len(names)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, help="directory holding the already-downloaded input files")
    args = ap.parse_args(argv)
    build(args.src)


if __name__ == "__main__":
    main()

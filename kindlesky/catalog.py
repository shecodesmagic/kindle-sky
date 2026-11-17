"""Vendored star and constellation data (see data/SOURCES.md)."""
import csv
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class Stars:
    hip: np.ndarray       # int
    ra_deg: np.ndarray    # ICRS / J2000
    dec_deg: np.ndarray
    mag: np.ndarray

    def index(self) -> dict[int, int]:
        return {int(h): i for i, h in enumerate(self.hip)}


@dataclass(frozen=True)
class Constellation:
    abbr: str
    name: str
    ra_deg: float          # label position
    dec_deg: float
    rank: int              # 1 = major, 3 = minor (from d3-celestial)


@lru_cache(maxsize=1)
def stars() -> Stars:
    with open(DATA / "stars.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return Stars(
        hip=np.array([int(r["hip"]) for r in rows]),
        ra_deg=np.array([float(r["ra_deg"]) for r in rows]),
        dec_deg=np.array([float(r["dec_deg"]) for r in rows]),
        mag=np.array([float(r["mag"]) for r in rows]),
    )


@lru_cache(maxsize=1)
def lines() -> dict[str, list[list[int]]]:
    """Constellation figures: abbreviation -> list of polylines of HIP numbers."""
    return json.loads((DATA / "lines.json").read_text())


@lru_cache(maxsize=1)
def constellations() -> list[Constellation]:
    raw = json.loads((DATA / "constellations.json").read_text())
    return [Constellation(c["abbr"], c["name"], c["ra"], c["dec"], c["rank"]) for c in raw]


@lru_cache(maxsize=1)
def star_names() -> dict[int, str]:
    return {int(k): v for k, v in json.loads((DATA / "starnames.json").read_text()).items()}


@lru_cache(maxsize=1)
def line_star_hips() -> frozenset[int]:
    return frozenset(h for polys in lines().values() for poly in polys for h in poly)

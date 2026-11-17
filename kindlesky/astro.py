"""Apparent positions (altitude/azimuth) for the observer, using Skyfield + JPL DE421."""
import math
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import skyfield_data
from skyfield import almanac
from skyfield.api import Star, load, load_file, wgs84

from . import catalog, config


@dataclass
class Body:
    name: str
    alt: float            # degrees, refracted
    az: float             # degrees from north through east
    phase: float = 0.0    # Moon only: illuminated fraction 0..1


@dataclass
class SkyState:
    when: datetime
    star_alt: np.ndarray
    star_az: np.ndarray
    planets: list[Body]
    moon: Body | None
    sun: Body | None
    const_alt: np.ndarray = None   # constellation label positions, same order as catalog.constellations()
    const_az: np.ndarray = None
    errors: list[str] = field(default_factory=list)


def ephemeris_path() -> Path:
    return Path(skyfield_data.__file__).resolve().parent / "data" / "de421.bsp"


@lru_cache(maxsize=1)
def _ephemeris():
    return load_file(str(ephemeris_path()))


@lru_cache(maxsize=1)
def _timescale():
    return load.timescale(builtin=True)   # bundled leap seconds / delta-T; no download


@lru_cache(maxsize=1)
def _star_objects():
    s = catalog.stars()
    return Star(ra_hours=s.ra_deg / 15.0, dec_degrees=s.dec_deg)


@lru_cache(maxsize=1)
def _label_objects():
    cs = catalog.constellations()
    return Star(ra_hours=np.array([c.ra_deg for c in cs]) / 15.0, dec_degrees=np.array([c.dec_deg for c in cs]))


def _altaz(apparent):
    alt, az, _ = apparent.altaz(temperature_C=config.TEMPERATURE_C, pressure_mbar=config.PRESSURE_MBAR)
    return alt.degrees, az.degrees


def compute(when: datetime, lat: float = config.LAT, lon: float = config.LON) -> SkyState:
    """Positions of stars, planets, Moon and Sun at `when` (timezone-aware)."""
    if when.tzinfo is None:
        raise ValueError("when must be timezone-aware")
    eph = _ephemeris()
    t = _timescale().from_datetime(when)
    observer = eph["earth"] + wgs84.latlon(lat, lon)
    here = observer.at(t)
    errors: list[str] = []

    star_alt, star_az = _altaz(here.observe(_star_objects()).apparent())
    bad = ~(np.isfinite(star_alt) & np.isfinite(star_az))
    if bad.any():                      # never seen, but a NaN must not reach the drawing code
        errors.append(f"{int(bad.sum())} star positions were not finite")
        star_alt = np.where(bad, -90.0, star_alt)
        star_az = np.where(bad, 0.0, star_az)

    const_alt, const_az = _altaz(here.observe(_label_objects()).apparent())

    def body(name, key):
        try:
            alt, az = _altaz(here.observe(eph[key]).apparent())
            alt, az = float(alt), float(az)
            if not (math.isfinite(alt) and math.isfinite(az)):
                raise ValueError("position is not finite")
            return Body(name, alt, az)
        except Exception as e:  # noqa: BLE001 - one bad body must not stop the others
            errors.append(f"{name}: {type(e).__name__}: {e}")
            return None

    planets = [b for b in (body(n, k) for n, k in config.PLANETS.items()) if b is not None]
    moon = body("Moon", "moon")
    if moon is not None:
        try:
            moon.phase = float(almanac.fraction_illuminated(eph, "moon", t))
        except Exception as e:  # noqa: BLE001
            errors.append(f"Moon phase: {type(e).__name__}: {e}")
            moon.phase = 0.5
    sun = body("Sun", "sun")
    return SkyState(when, star_alt, star_az, planets, moon, sun, const_alt, const_az, errors)

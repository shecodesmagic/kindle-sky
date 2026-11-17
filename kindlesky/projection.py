"""Stereographic projections from altitude/azimuth to screen pixels.

Stereographic is what Stellarium uses by default. It keeps constellation shapes
undistorted (it is conformal) and maps great circles through the centre to
straight lines, which is why the horizon in the south view is a straight line.
"""
from dataclasses import dataclass

import numpy as np


def altaz_to_enu(alt_deg, az_deg):
    """Unit vectors (east, north, up) for altitude/azimuth in degrees."""
    alt, az = np.radians(alt_deg), np.radians(az_deg)
    return np.cos(alt) * np.sin(az), np.cos(alt) * np.cos(az), np.sin(alt)


def enu_to_altaz(e, n, u):
    alt = np.degrees(np.arcsin(np.clip(u, -1.0, 1.0)))
    az = np.degrees(np.arctan2(e, n)) % 360.0
    return alt, az


@dataclass(frozen=True)
class SouthView:
    """Standing outside facing south: horizon along the bottom, west on the right.

    Centred on the south point of the horizon at pixel (cx, cy). `scale` is
    pixels per unit of the stereographic plane, so the zenith is 2*scale px
    above the horizon and an azimuth a degrees from south sits 2*scale*tan(a/2)
    px from the centre along the horizon.
    """
    cx: float
    cy: float
    scale: float

    def project(self, alt_deg, az_deg):
        e, n, u = altaz_to_enu(alt_deg, az_deg)
        x, y, z = -e, u, -n            # right = west, up = up, forward = south
        k = 2.0 / np.maximum(1.0 + z, 1e-9)
        return self.cx + self.scale * k * x, self.cy - self.scale * k * y

    def unproject(self, px, py):
        x = (np.asarray(px, float) - self.cx) / self.scale
        y = (self.cy - np.asarray(py, float)) / self.scale
        r2 = x * x + y * y
        d = 4.0 + r2
        vx, vy, vz = 4.0 * x / d, 4.0 * y / d, (4.0 - r2) / d
        return enu_to_altaz(-vx, -vz, vy)

    def in_front(self, alt_deg, az_deg):
        """True where projecting is numerically safe (not near the point behind you)."""
        _, n, _ = altaz_to_enu(alt_deg, az_deg)
        return -n > -0.9


@dataclass(frozen=True)
class OverheadView:
    """Lying on your back, head to the north: zenith in the centre, the horizon a
    circle of radius `radius`, north up, east LEFT and west right (a sky chart
    is the mirror image of a map because you look up at it, not down)."""
    cx: float
    cy: float
    radius: float

    def project(self, alt_deg, az_deg):
        z = np.radians(90.0 - np.asarray(alt_deg, float))
        r = self.radius * np.tan(z / 2.0)      # = 2R tan(z/2) with horizon at 2R
        az = np.radians(az_deg)
        return self.cx - r * np.sin(az), self.cy - r * np.cos(az)

    def unproject(self, px, py):
        dx = np.asarray(px, float) - self.cx
        dy = np.asarray(py, float) - self.cy
        r = np.hypot(dx, dy)
        alt = 90.0 - np.degrees(2.0 * np.arctan(r / self.radius))
        az = np.degrees(np.arctan2(-dx, -dy)) % 360.0
        return alt, az

    def in_front(self, alt_deg, az_deg):
        return np.asarray(alt_deg, float) > -60.0

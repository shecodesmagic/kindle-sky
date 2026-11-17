"""Draw one view of the sky as a 1072x1448 8-bit grayscale image for e-ink.

Every layer is drawn inside `_safe`, so an exception in one layer (or for one
planet) is logged in `Chart.errors` and the rest of the image is still produced.
"""
import itertools
import math
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import catalog, config
from .astro import SkyState
from .projection import OverheadView, SouthView, altaz_to_enu, enu_to_altaz

S = 2                        # supersampling factor; drawn at 2x then reduced
FONTS = Path(__file__).resolve().parent / "fonts"

# Grey levels (0 = black, 255 = white). Black sky, white stars.
SKY, STAR, LINE, CONST_LABEL, GRID, GROUND, TEXT = 0, 255, 150, 200, 70, 85, 255

VIEWS = ("south", "overhead")
TITLES = {"south": "LOOKING SOUTH", "overhead": "OVERHEAD"}


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONTS / name), size * S)


def star_radius(mag) -> np.ndarray:
    """Dot radius in (1x) pixels: brighter stars are bigger. Never below 1.6 px."""
    return np.clip(7.0 - 1.1 * np.asarray(mag, float), 1.6, 9.0)


def local_time(dt: datetime) -> str:
    t = dt.astimezone(ZoneInfo(config.TIMEZONE))
    return f"{t:%a %b} {t.day}, {t.hour % 12 or 12}:{t:%M %p} {t:%Z}"


def short_time(dt: datetime) -> str:
    t = dt.astimezone(ZoneInfo(config.TIMEZONE))
    return f"{t.hour % 12 or 12}:{t:%M %p}"


def compass(az: float) -> str:
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int(((az % 360) + 22.5) // 45) % 8]


@dataclass
class Placer:
    """Greedy label placement: try positions around a point, avoid overlaps."""
    taken: list = field(default_factory=list)
    bounds: tuple = (0, 0, config.W * S, config.H * S)

    def free(self, box) -> bool:
        x0, y0, x1, y1 = box
        bx0, by0, bx1, by1 = self.bounds
        if x0 < bx0 or y0 < by0 or x1 > bx1 or y1 > by1:
            return False
        return all(x1 <= a0 or x0 >= a1 or y1 <= b0 or y0 >= b1 for a0, b0, a1, b1 in self.taken)

    def reserve(self, box):
        self.taken.append(tuple(box))

    def place(self, draw, x, y, text, fnt, gap, force=False):
        """Return the top-left corner used, or None if no free spot (unless force)."""
        l, t, r, b = draw.textbbox((0, 0), text, font=fnt)
        w, h = r - l, b - t
        cands = [(x + gap, y - h / 2), (x - gap - w, y - h / 2), (x - w / 2, y - gap - h),
                 (x - w / 2, y + gap), (x + gap, y - gap - h), (x + gap, y + gap),
                 (x - gap - w, y - gap - h), (x - gap - w, y + gap)]
        bx0, by0, bx1, by1 = self.bounds
        for cx, cy in cands:
            box = (cx, cy, cx + w, cy + h)
            if self.free(box):
                self.reserve(box)
                return cx - l, cy - t
        if not force:
            return None
        cx = min(max(cands[0][0], bx0), bx1 - w)          # keep it on the chart
        cy = min(max(cands[0][1], by0), by1 - h)
        self.reserve((cx, cy, cx + w, cy + h))
        return cx - l, cy - t


class Chart:
    def __init__(self, view_name: str, sky: SkyState, rendered_at: datetime):
        if view_name not in VIEWS:
            raise ValueError(f"unknown view {view_name!r}")
        self.view_name, self.sky, self.rendered_at = view_name, sky, rendered_at
        self.errors: list[str] = list(sky.errors)
        self.img = Image.new("L", (config.W * S, config.H * S), SKY)
        self.d = ImageDraw.Draw(self.img)
        if view_name == "south":
            self.horizon_y = 1196
            # zenith 2*scale above the horizon, about 30 px below the header
            self.view = SouthView(config.W / 2 * S, self.horizon_y * S, (self.horizon_y - config.HEADER_H - 30) / 2 * S)
            self.chart_box = (0, config.HEADER_H * S, config.W * S, self.horizon_y * S)
        else:
            r = 450
            self.cy = config.HEADER_H + 64 + r
            self.view = OverheadView(config.W / 2 * S, self.cy * S, r * S)
            self.chart_box = (0, config.HEADER_H * S, config.W * S, config.FOOTER_Y * S)
        self.placer = Placer(bounds=self.chart_box)
        self.drawn_hips: set[int] = set()       # stars actually drawn (for tests)
        self.planet_labels: dict[str, tuple] = {}
        self.moon_xy = None

    # ----------------------------------------------------------------- helpers
    def _safe(self, name, fn, *a):
        try:
            fn(*a)
        except Exception as e:  # noqa: BLE001 - one failing layer must not lose the image
            self.errors.append(f"{self.view_name}/{name}: {type(e).__name__}: {e}")
            traceback.print_exc()

    def visible(self, alt, az):
        """Above the horizon and inside the chart area."""
        alt, az = np.asarray(alt, float), np.asarray(az, float)
        x, y = self.view.project(alt, az)
        x0, y0, x1, y1 = self.chart_box
        ok = (alt >= 0) & self.view.in_front(alt, az) & (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
        return ok, x, y

    def text_outlined(self, xy, text, fnt, fill=TEXT, outline=SKY, width=3):
        self.d.text(xy, text, font=fnt, fill=fill, stroke_width=width * S // 2 + 1, stroke_fill=outline)

    # ------------------------------------------------------------------ layers
    def grid(self):
        alts = np.linspace(0, 90, 181)
        if isinstance(self.view, OverheadView):
            for a in (30, 60):
                x, y = self.view.project(np.full(361, a), np.arange(361))
                self.d.line(list(zip(x, y)), fill=GRID, width=S)
            for az in range(0, 360, 45):
                x, y = self.view.project(alts, np.full_like(alts, az))
                self.d.line(list(zip(x, y)), fill=GRID, width=S)
        else:
            for a in (30, 60):
                azs = np.linspace(90, 270, 361)
                ok, x, y = self.visible(np.full_like(azs, a), azs)
                pts = [(float(px), float(py)) for px, py, k in zip(x, y, ok) if k]
                if len(pts) > 1:
                    self.d.line(pts, fill=GRID, width=S)
                    lx, ly = pts[0]
                    self.d.text((lx + 8 * S, ly - 30 * S), f"{a}°", font=font(22), fill=GRID + 60)
            for az in (135, 157.5, 180, 202.5, 225):
                ok, x, y = self.visible(alts, np.full_like(alts, az))
                pts = [(float(px), float(py)) for px, py, k in zip(x, y, ok) if k]
                if len(pts) > 1:
                    self.d.line(pts, fill=GRID, width=S)
            zx, zy = self.view.project(90.0, 180.0)
            self.d.line([(zx - 12 * S, zy), (zx + 12 * S, zy)], fill=GRID + 60, width=S)
            self.d.line([(zx, zy - 12 * S), (zx, zy + 12 * S)], fill=GRID + 60, width=S)
            self.d.text((zx + 14 * S, zy - 4 * S), "zenith", font=font(22), fill=GRID + 60)

    def star_mask(self):
        s = catalog.stars()
        limit = config.MAG_LIMIT[self.view_name]
        line = np.isin(s.hip, list(catalog.line_star_hips()))
        return (s.mag <= limit) | line

    def constellation_lines(self):
        s, idx = catalog.stars(), catalog.stars().index()
        alt, az = self.sky.star_alt, self.sky.star_az
        x, y = self.view.project(alt, az)
        front = self.view.in_front(alt, az)
        rad = star_radius(s.mag) * S
        seen = set()
        for polys in catalog.lines().values():
            for poly in polys:
                for h1, h2 in itertools.pairwise(poly):
                    key = (min(h1, h2), max(h1, h2))
                    if key in seen:
                        continue
                    seen.add(key)
                    i, j = idx[h1], idx[h2]
                    if alt[i] < 0 and alt[j] < 0:
                        continue
                    if not (front[i] and front[j]):
                        continue
                    x1, y1, x2, y2 = x[i], y[i], x[j], y[j]
                    length = math.hypot(x2 - x1, y2 - y1)
                    g1, g2 = rad[i] + 5 * S, rad[j] + 5 * S      # small gap around each star
                    if length <= g1 + g2 + 2 * S:
                        continue
                    ux, uy = (x2 - x1) / length, (y2 - y1) / length
                    self.d.line([(x1 + ux * g1, y1 + uy * g1), (x2 - ux * g2, y2 - uy * g2)], fill=LINE, width=2 * S)

    def stars(self):
        s = catalog.stars()
        ok, x, y = self.visible(self.sky.star_alt, self.sky.star_az)
        ok &= self.star_mask()
        rad = star_radius(s.mag) * S
        order = np.argsort(-s.mag)               # faint first, bright on top
        for i in order:
            if not ok[i]:
                continue
            r = rad[i]
            self.d.ellipse([x[i] - r, y[i] - r, x[i] + r, y[i] + r], fill=STAR)
            self.drawn_hips.add(int(s.hip[i]))
            if r >= 4 * S:                      # don't let labels cover bright stars
                self.placer.reserve((x[i] - r, y[i] - r, x[i] + r, y[i] + r))

    def star_labels(self):
        s, names = catalog.stars(), catalog.star_names()
        ok, x, y = self.visible(self.sky.star_alt, self.sky.star_az)
        for i in np.argsort(s.mag):
            h = int(s.hip[i])
            if not ok[i] or h not in names:
                continue
            if s.mag[i] > config.STAR_NAME_MAG and h not in config.ALWAYS_NAMED:
                continue
            pos = self.placer.place(self.d, x[i], y[i], names[h], font(28), star_radius(s.mag[i]) * S + 6 * S)
            if pos:
                self.text_outlined(pos, names[h], font(28), fill=TEXT)

    def constellation_labels(self):
        fnt = font(26)
        max_rank = 2 if self.view_name == "overhead" else 3
        cs = catalog.constellations()
        for i in sorted(range(len(cs)), key=lambda i: cs[i].rank):
            c = cs[i]
            if c.rank > max_rank:
                continue
            alt, az = float(self.sky.const_alt[i]), float(self.sky.const_az[i])
            ok, x, y = self.visible(alt, az)
            if not bool(ok) or alt < 4:
                continue
            text = c.name.upper()
            l, t, r, b = self.d.textbbox((0, 0), text, font=fnt)
            box = (x - (r - l) / 2, y - (b - t) / 2, x + (r - l) / 2, y + (b - t) / 2)
            if self.placer.free(box):
                self.placer.reserve(box)
                self.d.text((box[0] - l, box[1] - t), text, font=fnt, fill=CONST_LABEL)

    def moon(self):
        m = self.sky.moon
        if m is None:
            return
        ok, x, y = self.visible(m.alt, m.az)
        if not bool(ok):
            return
        x, y, r = float(x), float(y), 20 * S
        # Direction of the bright limb on screen: from the Moon towards the Sun.
        ux, uy = 0.0, -1.0
        if self.sky.sun is not None:
            mv = np.array(altaz_to_enu(m.alt, m.az))
            sv = np.array(altaz_to_enu(self.sky.sun.alt, self.sky.sun.az))
            d = sv - mv.dot(sv) * mv
            if np.linalg.norm(d) > 1e-9:
                d /= np.linalg.norm(d)
                p = mv * math.cos(math.radians(2)) + d * math.sin(math.radians(2))
                palt, paz = enu_to_altaz(*p)
                px, py = self.view.project(palt, paz)
                n = math.hypot(px - x, py - y)
                if n > 1e-9:
                    ux, uy = (px - x) / n, (py - y) / n
        vx, vy = -uy, ux
        self.d.ellipse([x - r, y - r, x + r, y + r], fill=SKY, outline=STAR, width=2 * S)
        k = min(max(m.phase, 0.0), 1.0)
        th = np.radians(np.linspace(-90, 90, 61))
        limb = [(x + r * (math.cos(a) * ux + math.sin(a) * vx),
                 y + r * (math.cos(a) * uy + math.sin(a) * vy)) for a in th]
        term = [(x + r * (-(2 * k - 1) * math.cos(a) * ux + math.sin(a) * vx),
                 y + r * (-(2 * k - 1) * math.cos(a) * uy + math.sin(a) * vy)) for a in th[::-1]]
        if k > 0.01:
            self.d.polygon(limb + term, fill=STAR)
        self.placer.reserve((x - r, y - r, x + r, y + r))
        self.moon_xy = (x, y, r, k)

    def moon_label(self):
        if self.moon_xy is None:
            return
        x, y, r, k = self.moon_xy
        label = f"MOON {round(k * 100)}%"
        pos = self.placer.place(self.d, x, y, label, font(34, True), r + 8 * S, force=True)
        self.text_outlined(pos, label, font(34, True))

    def _visible_planets(self):
        return [p for p in self.sky.planets if bool(self.visible(p.alt, p.az)[0])]

    def planets(self):
        for p in self._visible_planets():
            self._safe(p.name, self._planet, p)

    def _planet(self, p):
        _, x, y = self.visible(p.alt, p.az)
        x, y = float(x), float(y)
        r = 11 * S
        # white disc with a black gap and a white ring: reads as "not a star"
        self.d.ellipse([x - r - 6 * S, y - r - 6 * S, x + r + 6 * S, y + r + 6 * S], outline=STAR, width=2 * S)
        self.d.ellipse([x - r - 3 * S, y - r - 3 * S, x + r + 3 * S, y + r + 3 * S], fill=SKY)
        self.d.ellipse([x - r, y - r, x + r, y + r], fill=STAR)
        # reserve the symbol so no label covers any planet
        self.placer.reserve((x - r - 6 * S, y - r - 6 * S, x + r + 6 * S, y + r + 6 * S))

    def planet_label_layer(self):
        for p in self._visible_planets():
            self._safe(p.name + " label", self._planet_label, p)
        sun = self.sky.sun
        if sun is not None and bool(self.visible(sun.alt, sun.az)[0]):
            self._planet_label(sun)

    def sun(self):
        """The Sun, when it is up: a disc with rays (daytime frames)."""
        sun = self.sky.sun
        if sun is None or not bool(self.visible(sun.alt, sun.az)[0]):
            return
        _, x, y = self.visible(sun.alt, sun.az)
        x, y, r = float(x), float(y), 16 * S
        for k in range(8):
            a = math.radians(k * 45)
            self.d.line([(x + math.cos(a) * (r + 5 * S), y + math.sin(a) * (r + 5 * S)),
                         (x + math.cos(a) * (r + 16 * S), y + math.sin(a) * (r + 16 * S))], fill=STAR, width=3 * S)
        self.d.ellipse([x - r, y - r, x + r, y + r], fill=STAR)
        self.placer.reserve((x - r - 16 * S, y - r - 16 * S, x + r + 16 * S, y + r + 16 * S))

    def _planet_label(self, p):
        _, x, y = self.visible(p.alt, p.az)
        label = p.name.upper()
        pos = self.placer.place(self.d, float(x), float(y), label, font(40, True), 23 * S, force=True)
        self.text_outlined(pos, label, font(40, True), width=4)
        self.planet_labels[p.name] = pos

    def horizon_and_ground(self):
        if isinstance(self.view, OverheadView):
            # blank everything outside the horizon circle
            mask = Image.new("L", self.img.size, 255)
            md = ImageDraw.Draw(mask)
            cx, cy, R = self.view.cx, self.view.cy, self.view.radius
            md.ellipse([cx - R, cy - R, cx + R, cy + R], fill=0)
            self.img.paste(SKY, mask=mask)
            self.d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=STAR, width=3 * S)
            f = font(40, True)
            for label, az in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
                rr = R + 34 * S
                x, y = cx - rr * math.sin(math.radians(az)), cy - rr * math.cos(math.radians(az))
                l, t, r, b = self.d.textbbox((0, 0), label, font=f)
                x0, y0 = x - (r - l) / 2, y - (b - t) / 2
                self.d.text((x0 - l, y0 - t), label, font=f, fill=TEXT)
                self.placer.reserve((x0, y0, x0 + r - l, y0 + b - t))
        else:
            y0 = self.horizon_y * S
            self.d.rectangle([0, y0, config.W * S, config.FOOTER_Y * S], fill=GROUND)
            self.d.line([(0, y0), (config.W * S, y0)], fill=STAR, width=3 * S)
            f = font(36, True)
            for label, az in (("SE", 135), ("S", 180), ("SW", 225)):
                x, _ = self.view.project(0.0, az)
                if 0 <= x <= config.W * S:
                    self.d.line([(x, y0), (x, y0 + 14 * S)], fill=STAR, width=2 * S)
                    l, t, r, b = self.d.textbbox((0, 0), label, font=f)
                    self.d.text((x - (r + l) / 2, y0 + 18 * S - t), label, font=f, fill=TEXT)

    def notes(self):
        """Where the planets are that this view doesn't show, and a daylight warning."""
        lines = []
        sun = self.sky.sun
        if sun is not None and sun.alt > -6:
            lines.append("Daytime: the Sun is up, so these stars can't be seen now.")
        below, elsewhere = [], []
        for p in self.sky.planets:
            if bool(self.visible(p.alt, p.az)[0]):
                continue
            if p.alt < 0:
                below.append(p.name)
            else:
                elsewhere.append(f"{p.name} ({compass(p.az)}, {round(p.alt)}° up)")
        if elsewhere:
            lines.append("Out of view: " + ", ".join(elsewhere))
        if below:
            lines.append("Below horizon: " + ", ".join(below))
        if not lines:
            return
        f = font(26)
        if self.view_name == "south":
            y = (self.horizon_y + 62) * S
            lines = lines[:2]
        else:
            y = (config.FOOTER_Y - 34 * len(lines) - 8) * S
        for text in lines:
            while self.d.textlength(text, font=f) > (config.W - 40) * S and ", " in text:
                text = text.rsplit(", ", 1)[0] + " …"
            self.d.text((20 * S, y), text, font=f, fill=TEXT)
            y += 34 * S

    def header(self):
        self.d.rectangle([0, 0, config.W * S, config.HEADER_H * S], fill=SKY)
        title = f"{config.PLACE.upper()} · {TITLES[self.view_name]}"
        self.d.text((24 * S, 14 * S), title, font=font(44, True), fill=TEXT)
        self.d.text((24 * S, 72 * S), f"Sky at {local_time(self.sky.when)}  ·  rendered {short_time(self.rendered_at)}",
                    font=font(30), fill=TEXT)
        y = (config.HEADER_H - 2) * S
        self.d.line([(0, y), (config.W * S, y)], fill=GRID + 60, width=S)

    def footer(self):
        y0 = config.FOOTER_Y * S
        self.d.rectangle([0, y0, config.W * S, config.H * S], fill=SKY)
        self.d.line([(0, y0), (config.W * S, y0)], fill=STAR, width=2 * S)
        other = "overhead" if self.view_name == "south" else "looking south"
        f = font(32, True)
        self.d.text((24 * S, y0 + 22 * S), "Tap here: switch to", font=font(28), fill=TEXT)
        self.d.text((24 * S, y0 + 60 * S), other.upper(), font=f, fill=TEXT)
        x0 = config.EXIT_X * S
        self.d.rectangle([x0, y0 + 14 * S, config.W * S - 14 * S, config.H * S - 14 * S], outline=STAR, width=3 * S)
        l, t, r, b = self.d.textbbox((0, 0), "EXIT", font=font(48, True))
        cx, cy = (x0 + config.W * S - 14 * S) / 2, (y0 + config.H * S) / 2
        self.d.text((cx - (r + l) / 2, cy - (b + t) / 2), "EXIT", font=font(48, True), fill=TEXT)

    # ----------------------------------------------------------------- render
    def render(self) -> Image.Image:
        # symbols first, then the horizon (which clips them), then labels on top
        for name in ("grid", "constellation_lines", "stars", "moon", "planets", "sun", "horizon_and_ground",
                     "planet_label_layer", "moon_label", "star_labels", "constellation_labels",
                     "notes", "header", "footer"):
            self._safe(name, getattr(self, name))
        small = self.img.resize((config.W, config.H), Image.Resampling.LANCZOS)
        # 16 grey levels, which is all the e-ink panel shows; also shrinks the PNG
        return small.point(lambda v: min(255, (v + 8) // 17 * 17))


def render_view(view_name: str, sky: SkyState, rendered_at: datetime) -> tuple[Image.Image, list[str]]:
    chart = Chart(view_name, sky, rendered_at)
    img = chart.render()
    return img, chart.errors

"""Everything you might want to change in one place."""

# Observer. City-level only (one decimal place is about 10 km): central Ottawa.
PLACE = "Ottawa"
LAT = 45.4
LON = -75.7
TIMEZONE = "America/Toronto"

# Kindle Paperwhite 3 (7th gen): 1072 x 1448 px portrait, 300 ppi, 16 grey levels.
W, H = 1072, 1448

# Frames: one image per view every STEP_MIN minutes, from one step before "now"
# to HOURS_AHEAD hours ahead. The Kindle picks the frame for its own clock, so
# the picture stays current even if a scheduled GitHub run is late or skipped.
STEP_MIN = 5
HOURS_AHEAD = 3

# Screen layout (pixels). The Kindle script uses the same numbers to decide
# what a tap means, and tests/test_consistency.py checks that they agree.
HEADER_H = 118
FOOTER_Y = 1328          # footer band (tap zones) starts here
EXIT_X = 760             # EXIT box: x >= EXIT_X and y >= FOOTER_Y

# Magnitude limits. Stars on constellation lines are always drawn, whatever
# their magnitude, so that no line ends in empty space.
MAG_LIMIT = {"south": 5.0, "overhead": 4.5}
STAR_NAME_MAG = 1.5      # label stars brighter than this (plus Polaris)
ALWAYS_NAMED = {11767}   # Polaris (HIP)

# Naked-eye planets only; Uranus and Neptune need optical aid.
PLANETS = {
    "Mercury": "mercury",
    "Venus": "venus",
    "Mars": "mars",
    "Jupiter": "jupiter barycenter",
    "Saturn": "saturn barycenter",
}

# Standard atmosphere for refraction, as Stellarium does by default.
TEMPERATURE_C = 10.0
PRESSURE_MBAR = 1010.0

# Hard limit the Kindle enforces on downloads; validate.py checks we stay under it.
MAX_PNG_BYTES = 2_000_000

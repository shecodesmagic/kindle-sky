# shellcheck shell=sh disable=SC2034  # settings file sourced by skyd.sh
# kindle-sky settings. This file is read by skyd.sh as root, so keep it to
# plain VAR=value lines.

# Where the GitHub Pages site lives: https://<github-user>.github.io/<repo>
BASE_URL="https://shecodesmagic.github.io/kindle-sky"

# View shown when sky mode starts: south or overhead
DEFAULT_VIEW=south

# Touchscreen mapping. Every tap is logged in kindle-sky.log as
#   tap raw=(x,y) screen=(x,y)
# If tapping EXIT switches the view instead, compare those numbers with
# where you tapped and adjust these (see docs/SETUP.md, "Touch calibration").
TOUCH_DEV=/dev/input/event1
TOUCH_MAX_X=1072
TOUCH_MAX_Y=1448
TOUCH_SWAP_XY=0
TOUCH_MIRROR_X=0
TOUCH_MIRROR_Y=0

# Give the Kindle back automatically after this many seconds (0 = never).
MAX_RUNTIME=43200

# Optional: a CA certificate bundle (PEM) for curl, if the Kindle's own
# certificate store can't verify github.io (curl exit 60 in the log).
CA_BUNDLE=""

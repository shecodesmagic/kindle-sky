#!/bin/sh
# kindle-sky: show the live sky full-screen on a jailbroken Kindle Paperwhite 3.
#
#   sh skyd.sh launch   start sky mode (what the "Sky Mode" scriptlet runs)
#   sh skyd.sh stop     stop sky mode (e.g. over SSH)
#   sh skyd.sh status   say whether it is running
#   sh skyd.sh run      the sky-mode process itself (started by "launch")
#
# While sky mode runs, the Kindle UI service is stopped (Upstart job
# lab126_gui, as KOReader's "no framework" mode does) so nothing draws over
# the sky, and restarted on exit. Tap the screen to switch views; tap the
# EXIT box (bottom right), tap three times within 2 s, or press the power
# button to go back to the normal Kindle. It also exits after MAX_RUNTIME.
#
# Safety rules this script keeps:
#  - it only ever signals processes it started itself (PIDs it recorded,
#    checked against /proc start time and command line);
#  - it only downloads over HTTPS with certificate checks (never -k), and only
#    displays a file that is a 1072x1448 8-bit grayscale PNG under 2 MB.

PATH="$PATH:/usr/sbin:/sbin:/usr/bin:/bin"

SKY_DIR=${KSKY_DIR:-/mnt/us/kindle-sky}
STATE=${KSKY_STATE:-/tmp/kindle-sky}
LOG=${KSKY_LOG:-$SKY_DIR/kindle-sky.log}
SLEEP=${KSKY_SLEEP:-sleep}    # tests swap in a faster clock
SELF=$0

# ---- settings; override any of these in config.sh next to this script ----
BASE_URL="https://shecodesmagic.github.io/kindle-sky"
DEFAULT_VIEW=south
TOUCH_DEV=/dev/input/event1   # PW3 touchscreen (per KOReader's device table)
EVENT_SIZE=16                 # bytes per input_event on a 32-bit kernel
TOUCH_MAX_X=1072              # raw touch range; screen is 1072 x 1448
TOUCH_MAX_Y=1448
TOUCH_SWAP_XY=0
TOUCH_MIRROR_X=0
TOUCH_MIRROR_Y=0
RETRY=60                      # seconds before retrying a failed download
MAX_RUNTIME=43200             # give the Kindle back after 12 h (0 = never)
CA_BUNDLE=""                  # optional PEM file for curl --cacert
LAUNCH_SETTLE=3               # let the scriptlet launcher finish first
UI_SETTLE=2                   # let the UI finish shutting down
FBINK_CANDIDATES="/mnt/us/libkh/bin/fbink /usr/bin/fbink /mnt/us/koreader/fbink"
# shellcheck source=/dev/null
[ -f "$SKY_DIR/config.sh" ] && . "$SKY_DIR/config.sh"

# ---- fixed: must match kindlesky/config.py (checked by tests) ----
W=1072
H=1448
STEP=300
FOOTER_Y=1328
EXIT_X=760
MAX_BYTES=2000000
MIN_BYTES=1000
LOG_MAX=200000
# First 29 bytes (octal) of a 1072x1448, 8-bit, grayscale, non-interlaced PNG.
PNG_HEADER="211 120 116 107 015 012 032 012 000 000 000 015 111 110 104 122 000 000 004 060 000 000 005 250 010 000 000 000 000"

PIDFILE=$STATE/skyd.pid
VIEWFILE=$STATE/view
EXITFILE=$STATE/exit

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

trim_log() {
    if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt "$LOG_MAX" ]; then
        tail -n 500 "$LOG" > "$LOG.tmp" && mv -f "$LOG.tmp" "$LOG"
    fi
}

# Start time (clock ticks since boot) of a process, field 22 of /proc/PID/stat.
# The command name in field 2 may contain spaces, so cut after the last ")".
proc_start() {
    sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | cut -d' ' -f20
}

# True only if PID is still the sky-mode process that wrote the PID file.
is_ours() {
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    case "$2" in ''|*[!0-9]*) return 1 ;; esac
    [ "$(proc_start "$1")" = "$2" ] || return 1
    tr '\000' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -q 'skyd\.sh run'
}

# True if PID is a live (not zombie) child of this sky-mode process. A helper
# that exited long ago may have had its PID reused by an unrelated process,
# so never signal a recorded helper PID without checking this first.
our_child() {
    st=$(sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | cut -d' ' -f1,2)
    [ "${st#* }" = "$MAIN_PID" ] && [ "${st% *}" != Z ]
}

running_pid() {
    [ -f "$PIDFILE" ] || return 1
    read -r rpid rstart < "$PIDFILE" || return 1
    is_ours "$rpid" "$rstart" || return 1
    echo "$rpid"
}

find_fbink() {
    for f in $FBINK_CANDIDATES; do
        if [ -x "$f" ]; then echo "$f"; return 0; fi
    done
    return 1
}

show_image() {
    # Simple lock so the refresh and touch helpers never draw at the same time.
    n=0
    until mkdir "$STATE/draw.lock" 2>/dev/null; do
        n=$((n + 1))
        if [ "$n" -ge 10 ]; then rmdir "$STATE/draw.lock" 2>/dev/null; n=0; fi
        "$SLEEP" 1
    done
    if [ -n "$FBINK" ]; then
        "$FBINK" -q -f -g file="$1" >/dev/null 2>&1
    else
        eips -f -g "$1" >/dev/null 2>&1
    fi
    rmdir "$STATE/draw.lock" 2>/dev/null
}

show_message() {
    if [ -n "$FBINK" ]; then
        "$FBINK" -q -m -y 20 "$1" >/dev/null 2>&1
    else
        eips 2 20 "$1" >/dev/null 2>&1
    fi
}

# Is $1 a PNG of the exact size and type we publish?
png_ok() {
    [ -f "$1" ] || return 1
    size=$(wc -c < "$1")
    [ "$size" -ge "$MIN_BYTES" ] && [ "$size" -le "$MAX_BYTES" ] || return 1
    # od -b prints octal bytes; keep only the 3-digit fields (drops addresses).
    hdr=$(dd if="$1" bs=29 count=1 2>/dev/null | od -v -b |
        awk '{ for (i = 1; i <= NF; i++) if (length($i) == 3) printf "%s ", $i }')
    [ "$hdr" = "$PNG_HEADER " ]
}

current_view() {
    v=$(cat "$VIEWFILE" 2>/dev/null)
    case "$v" in south|overhead) echo "$v" ;; *) echo "$DEFAULT_VIEW" ;; esac
}

other_view() {
    if [ "$1" = south ]; then echo overhead; else echo south; fi
}

# Download one frame; keep it only if it passes png_ok. Returns curl's status
# (or 99 if the file was rejected).
fetch() {
    view=$1
    url="$BASE_URL/frames/$view-$2.png"
    part="$STATE/$view.part"
    rm -f "$part"
    set -- --fail --silent --show-error --proto =https --connect-timeout 20 --max-time 60 \
        --max-filesize "$MAX_BYTES"
    if [ -n "$CA_BUNDLE" ]; then set -- "$@" --cacert "$CA_BUNDLE"; fi
    curl "$@" -o "$part" "$url" 2>> "$LOG" &
    CHILD=$!
    wait "$CHILD"
    rc=$?
    CHILD=""
    if [ "$rc" -ne 0 ]; then
        log "download failed: $view (curl exit $rc) $url"
        rm -f "$part"
        return "$rc"
    fi
    if ! png_ok "$part"; then
        log "rejected download: $view is not a ${W}x${H} grayscale PNG under $MAX_BYTES bytes"
        rm -f "$part"
        return 99
    fi
    mv -f "$part" "$STATE/$view.png"
    return 0
}

request_exit() {
    echo "$1" > "$EXITFILE"
    kill -TERM "$MAIN_PID" 2>/dev/null
}

nap() {   # interruptible sleep inside a helper
    "$SLEEP" "$1" &
    CHILD=$!
    wait "$CHILD"
    CHILD=""
}

refresh_loop() {
    trap '[ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null; exit 0' TERM
    CHILD=""
    while :; do
        now=$(date +%s)
        slot=$((now / STEP * STEP))
        view=$(current_view)
        if fetch "$view" "$slot"; then
            show_image "$STATE/$view.png"
            ok=1
        else
            ok=0
        fi
        # the other view too, so switching is instant (and works offline)
        other=$(other_view "$view")
        if fetch "$other" "$slot"; then
            # a tap may have switched to it while we were downloading
            [ "$(current_view)" = "$other" ] && show_image "$STATE/$other.png"
        else
            ok=0
        fi
        next=$((slot + STEP + 5))
        if [ "$ok" -eq 0 ] && [ $((now + RETRY)) -lt "$next" ]; then
            next=$((now + RETRY))
        fi
        wait_s=$((next - $(date +%s)))
        [ "$wait_s" -lt 1 ] && wait_s=1
        nap "$wait_s"
    done
}

# Decode raw input_event records (little-endian) into "sec type code value".
decode_events() {
    od -v -b "$1" | awk -v es="$EVENT_SIZE" '
        function oct(s,   i, n) { n = 0; for (i = 1; i <= length(s); i++) n = n * 8 + substr(s, i, 1); return n }
        { for (i = 1; i <= NF; i++) if (length($i) == 3) b[n++] = oct($i) }
        END {
            t = es - 8
            for (o = 0; o + es <= n; o += es) {
                sec = b[o] + 256 * b[o + 1] + 65536 * b[o + 2] + 16777216 * b[o + 3]
                type = b[o + t] + 256 * b[o + t + 1]
                code = b[o + t + 2] + 256 * b[o + t + 3]
                value = b[o + t + 4] + 256 * b[o + t + 5] + 65536 * b[o + t + 6] + 16777216 * b[o + t + 7]
                printf "%d %d %d %.0f\n", sec, type, code, value
            }
        }'
}

handle_tap() {   # raw x, raw y, event time (s)
    tx=$1
    ty=$2
    if [ "$TOUCH_SWAP_XY" = 1 ]; then tx=$2; ty=$1; fi
    sx=$((tx * W / TOUCH_MAX_X))
    sy=$((ty * H / TOUCH_MAX_Y))
    [ "$TOUCH_MIRROR_X" = 1 ] && sx=$((W - 1 - sx))
    [ "$TOUCH_MIRROR_Y" = 1 ] && sy=$((H - 1 - sy))
    log "tap raw=($1,$2) screen=($sx,$sy)"
    t1=$t2
    t2=$t3
    t3=$3
    if [ -n "$t1" ] && [ $((t3 - t1)) -le 2 ]; then
        request_exit "triple tap"
        return
    fi
    if [ "$sx" -ge "$EXIT_X" ] && [ "$sy" -ge "$FOOTER_Y" ]; then
        request_exit "EXIT tapped"
        return
    fi
    new=$(other_view "$(current_view)")
    echo "$new" > "$VIEWFILE"
    log "view: $new"
    if [ -f "$STATE/$new.png" ]; then
        show_image "$STATE/$new.png"
    else
        show_message "Loading $new view..."
    fi
}

touch_loop() {
    trap '[ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null; exit 0' TERM
    CHILD=""
    x=0; y=0; lift=0; empty=0
    t1=""; t2=""; t3=""
    while :; do
        dd if="$TOUCH_DEV" of="$STATE/ev.bin" bs=$((EVENT_SIZE * 64)) count=1 2>/dev/null &
        CHILD=$!
        wait "$CHILD"
        CHILD=""
        if [ ! -s "$STATE/ev.bin" ]; then
            empty=$((empty + 1))
            if [ "$empty" -ge 30 ]; then
                log "touchscreen $TOUCH_DEV gives no data; leaving sky mode"
                request_exit "touchscreen unreadable"
                return
            fi
            nap 1
            continue
        fi
        empty=0
        decode_events "$STATE/ev.bin" > "$STATE/ev.txt"
        while read -r sec type code value; do
            case "$type $code" in
                "3 53"|"3 0") x=$value ;;                          # ABS_MT_POSITION_X / ABS_X
                "3 54"|"3 1") y=$value ;;                          # ABS_MT_POSITION_Y / ABS_Y
                "3 57") [ "$value" = 4294967295 ] && lift=1 ;;     # ABS_MT_TRACKING_ID -1: finger up
                "1 330") [ "$value" = 0 ] && lift=1 ;;             # BTN_TOUCH 0: finger up
                "0 0")                                             # SYN_REPORT
                    if [ "$lift" = 1 ]; then
                        lift=0
                        handle_tap "$x" "$y" "$sec"
                    fi ;;
            esac
        done < "$STATE/ev.txt"
    done
}

power_loop() {
    trap '[ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null; exit 0' TERM
    CHILD=""
    lipc-wait-event com.lab126.powerd goingToScreenSaver,outOfScreenSaver > "$STATE/power.txt" 2>&1 &
    CHILD=$!
    wait "$CHILD"
    CHILD=""
    if grep -q ScreenSaver "$STATE/power.txt"; then
        request_exit "power button"
    else
        log "power button watch unavailable: $(head -c 200 "$STATE/power.txt")"
    fi
}

preflight() {
    fail=0
    for c in curl dd od awk sed tr stop start lipc-set-prop; do
        if ! command -v "$c" >/dev/null 2>&1; then log "missing command: $c"; fail=1; fi
    done
    if [ -z "$FBINK" ] && ! command -v eips >/dev/null 2>&1; then log "no fbink or eips to draw with"; fail=1; fi
    if [ ! -r "$TOUCH_DEV" ]; then log "touchscreen $TOUCH_DEV not readable"; fail=1; fi
    case "$BASE_URL" in https://*) ;; *) log "BASE_URL must start with https://"; fail=1 ;; esac
    return "$fail"
}

cleanup() {
    trap '' TERM INT HUP
    [ -f "$EXITFILE" ] && log "leaving sky mode: $(cat "$EXITFILE")"
    for p in $HELPERS $MAIN_SLEEP; do
        our_child "$p" && kill -TERM "$p" 2>/dev/null
    done
    # wait (briefly) for helpers so none draws after the UI is back
    n=0
    for p in $HELPERS; do
        while our_child "$p" && [ "$n" -lt 20 ]; do n=$((n + 1)); "$SLEEP" 1; done
    done
    if [ "$UI_STOPPED" = 1 ]; then
        log "restarting Kindle UI"
        start lab126_gui >/dev/null 2>&1
    fi
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 >/dev/null 2>&1
    rm -f "$PIDFILE" "$EXITFILE" "$STATE/ev.bin" "$STATE/ev.txt"
    rmdir "$STATE/draw.lock" 2>/dev/null
    log "sky mode stopped"
}

cmd_run() {
    mkdir -p "$STATE"
    MAIN_PID=$$
    echo "$MAIN_PID $(proc_start "$MAIN_PID")" > "$PIDFILE"
    HELPERS=""
    MAIN_SLEEP=""
    UI_STOPPED=0
    rm -f "$EXITFILE"
    trap cleanup EXIT
    trap 'exit 0' TERM INT HUP
    FBINK=$(find_fbink)
    log "sky mode starting (pid $MAIN_PID, drawing with ${FBINK:-eips})"
    "$SLEEP" "$LAUNCH_SETTLE"
    lipc-set-prop com.lab126.powerd preventScreenSaver 1 >/dev/null 2>&1

    # Upstart sends TERM while stopping the UI; don't let that stop us.
    UI_STOPPED=1
    trap '' TERM
    stop lab126_gui >/dev/null 2>&1 || log "stop lab126_gui returned $?"
    "$SLEEP" "$UI_SETTLE"
    trap 'exit 0' TERM

    view=$(current_view)
    if [ -f "$STATE/$view.png" ]; then show_image "$STATE/$view.png"; else show_message "Loading the sky..."; fi

    refresh_loop & HELPERS="$HELPERS $!"
    touch_loop & HELPERS="$HELPERS $!"
    critical=$HELPERS
    if command -v lipc-wait-event >/dev/null 2>&1; then
        power_loop & HELPERS="$HELPERS $!"
    fi

    started=$(date +%s)
    while :; do
        "$SLEEP" 20 &
        MAIN_SLEEP=$!
        wait "$MAIN_SLEEP"
        MAIN_SLEEP=""
        if [ "$MAX_RUNTIME" -gt 0 ] && [ $(($(date +%s) - started)) -ge "$MAX_RUNTIME" ]; then
            log "MAX_RUNTIME reached"
            exit 0
        fi
        for p in $critical; do
            if ! our_child "$p"; then
                log "helper $p stopped unexpectedly"
                exit 1
            fi
        done
    done
}

cmd_launch() {
    mkdir -p "$STATE" "$SKY_DIR"
    trim_log
    if pid=$(running_pid); then
        log "already running (pid $pid); stopping it"
        kill -TERM "$pid"
        return 0
    fi
    FBINK=$(find_fbink)
    if ! preflight; then
        show_message "Sky mode cannot start: see kindle-sky.log"
        return 1
    fi
    # Own session, so signals aimed at the launcher's process group miss us.
    if command -v setsid >/dev/null 2>&1; then
        setsid sh "$SELF" run < /dev/null >> "$LOG" 2>&1 &
    elif command -v nohup >/dev/null 2>&1; then
        nohup sh "$SELF" run < /dev/null >> "$LOG" 2>&1 &
    else
        sh "$SELF" run < /dev/null >> "$LOG" 2>&1 &
    fi
    log "launched"
    return 0
}

cmd_stop() {
    if ! pid=$(running_pid); then
        echo "not running"
        return 0
    fi
    kill -TERM "$pid"
    n=0
    while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 30 ]; do n=$((n + 1)); "$SLEEP" 1; done
    echo "stopped"
}

case "$1" in
    launch) cmd_launch ;;
    run) cmd_run ;;
    stop) cmd_stop ;;
    status) if pid=$(running_pid); then echo "running (pid $pid)"; else echo "not running"; fi ;;
    *) echo "usage: sh $0 launch|stop|status" >&2; exit 2 ;;
esac

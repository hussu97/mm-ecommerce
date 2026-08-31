#!/bin/sh
# Run headed Chrome under a virtual X display when the command actually needs
# a browser. HTTP-only commands (heal-sessions, hydrate, store-account,
# mailbox-auth) skip Xvfb so a 2-minute heal tick that only checks the API
# does not pay for a virtual X server — and so a curl-gated start that finds
# the channel already in backoff does not open a display at all.
#
# The anti-bot edges the httpx channels sit behind — Noon (Akamai), Talabat
# (PerimeterX), Deliveroo (Cloudflare) — drop or stall HEADLESS Chrome at the
# TLS/sensor layer: a headless `page.goto` to restaurant.noon.partners never
# even commits (ERR_HTTP2_PROTOCOL_ERROR / 45s timeout), while a plain HTTP GET
# and a HEADED Chrome both load it 200. Verified on the prod VM 2026-08: a
# headed warm under Xvfb rotated Noon's Akamai cookie and pushed it back `live`
# where every headless attempt failed. So the browser side must have a display.
#
# xvfb-run allocates a fresh display (-a), runs the command under it, and tears
# the X server down when the command exits — no resident X server between the
# one-shot cron runs. HEADLESS=false (set in the image env) makes the Playwright
# launches actually use it.
#
# heal-sessions MUST have a display too: when it finds a dead anti-bot channel it
# drives the stored login, which spawns HEADED Chrome (HEADLESS=false). Without a
# display Chrome never opens its debug port and every anti-bot re-login fails as
# "Chrome did not open a debug port" — so the every-2-min heal could detect death
# but never fix noon/talabat/careem. Wrapping it in Xvfb closes that gap. (heal's
# no-op ticks still cost a short-lived X server; acceptable for the reliability.)
cmd="$1"
case "$cmd" in
    serve)
        # The always-on worker daemon (Phase 3). It is long-lived and spawns Chrome
        # per job, so — unlike the retired one-shot warms, each wrapped in its own
        # `xvfb-run` that tears the display down on exit — it needs ONE resident
        # virtual display that outlives every individual job. Start Xvfb in the
        # background and point Chrome at it; tini (init:true) reaps it on shutdown.
        # HEADLESS=false (image/compose env) makes the per-job Chrome launches use
        # this display. This is the only resident process besides the daemon; no
        # Chrome sits resident (that is the e2-small RAM guarantee).
        Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp >/dev/null 2>&1 &
        export DISPLAY=:99
        exec aggregator-bootstrap serve
        ;;
    login|warm-sessions|bootstrap|capture-and-push|serve-reauth|heal-sessions)
        exec xvfb-run -a --server-args="-screen 0 1280x1024x24 -nolisten tcp" \
            aggregator-bootstrap "$@"
        ;;
    *)
        exec aggregator-bootstrap "$@"
        ;;
esac

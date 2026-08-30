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
cmd="$1"
case "$cmd" in
    login|warm-sessions|bootstrap|capture-and-push|serve-reauth)
        exec xvfb-run -a --server-args="-screen 0 1280x1024x24 -nolisten tcp" \
            aggregator-bootstrap "$@"
        ;;
    *)
        exec aggregator-bootstrap "$@"
        ;;
esac

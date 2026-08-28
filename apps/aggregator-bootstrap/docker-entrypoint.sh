#!/bin/sh
# Run the worker under a virtual X display so real Chrome runs HEADED.
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
exec xvfb-run -a --server-args="-screen 0 1280x1024x24 -nolisten tcp" \
    aggregator-bootstrap "$@"

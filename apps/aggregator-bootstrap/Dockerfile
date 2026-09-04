# The browser-side worker. Runs as its own one-shot job (a daily cron), NOT with
# the API — Playwright lives only here, kept off the app VM's hot path.
#
# We deliberately do NOT use mcr.microsoft.com/playwright/python (the -noble tag
# was ~2.5GB uncompressed, ~4.7GB on the VM once layered): it bundles Chromium,
# Firefox AND WebKit plus every OS dependency for all three, and this worker
# drives exactly one of them — real branded Google Chrome, via `channel="chrome"`
# (Cloudflare must see branded Chrome, so chromium is not a substitute). So we
# start from python:3.12-slim and install ONLY Google Chrome and the OS libraries
# it needs. The Chrome binary is the same google-chrome-stable the old base
# installed with `playwright install chrome`, so the browser fingerprint is
# unchanged — only the two unused browsers and their deps are gone. That takes
# the image from ~4.7GB to ~1GB, which is what kept filling the 20GB VM disk.
#
# 3.12 specifically (not slim's newest): the package's requires-python is
# ">=3.12", and the old -noble base also shipped 3.12 — so the runtime does not
# move, only the browser bloat is dropped.
FROM python:3.12-slim

# `playwright install --with-deps chrome` adds Google's apt repo and pulls
# google-chrome-stable; it needs these present to fetch and verify the repo key.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Google Chrome + its OS deps + Xvfb, as a STABLE layer installed BEFORE any app
# code. This is the ~1GB of the image, and it must not depend on `src/`: it used
# to sit in the same RUN as `pip install .` (right after `COPY src`), so a one-line
# code change invalidated `COPY src`, invalidated this layer, and forced the VM to
# re-pull the whole ~1GB browser layer on every bootstrap deploy — ~9 min of dead
# time in the cutover. Installing it here, keyed only on this Dockerfile, means a
# code change never touches it and a code-only deploy pulls just the small app
# layer below.
#
# The Playwright python driver is installed explicitly first (matching pyproject's
# floor) because `playwright install` needs it; `pip install .` below reuses that
# same already-satisfied version, so the branded Google Chrome fetched here stays
# version-matched. We install ONLY Google Chrome (channel=chrome, a system apt
# package under /opt/google/chrome — which is why `rm -rf /root/.cache` is safe,
# that cache only holds the unused bundled chromium), not the firefox/webkit an
# all-browsers base would carry. `xvfb`/`xauth` give real Chrome a virtual display
# so it runs HEADED (anti-bot edges drop headless).
RUN pip install --no-cache-dir "playwright>=1.52" \
 && playwright install --with-deps chrome \
 && apt-get update \
 && apt-get install -y --no-install-recommends xvfb xauth \
 && rm -rf /var/lib/apt/lists/* /root/.cache

# App code + the package install — the ONLY layers a code change invalidates, and
# both tiny. `pip install .` reuses the Playwright driver + browser installed above.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# storage_state (the logged-in Playwright sessions) persists under /data, which
# MUST be a mounted volume in production (see the README "Operations" section) or
# every scheduled run starts logged-out. `pwuser` is recreated with **uid/gid
# 1001** to match the old Microsoft base byte-for-byte: the existing prod session
# volume is owned by 1001, and any other id would make the captured Keeta login
# unreadable — a silent logout that only a headed re-login from a laptop can undo.
RUN groupadd --gid 1001 pwuser \
 && useradd --uid 1001 --gid 1001 --create-home pwuser \
 && mkdir -p /data/sessions \
 && chown -R pwuser:pwuser /data
VOLUME ["/data"]

# The Xvfb wrapper (starts a virtual display, runs the CLI headed under it).
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Drop root: run as the unprivileged pwuser, exactly as the old image did.
USER pwuser

# Real Chrome must run HEADED to pass the anti-bot walls (see docker-entrypoint.sh
# and browser.py). The entrypoint provides the display; this makes the Playwright
# launches use it. A caller can still override `-e HEADLESS=true` for a headless run.
ENV HEADLESS=false

# Runs on a schedule (the /etc/cron.d Keeta pull + the anti-bot warm). Hydrates
# Playwright state from the API first so a new image with an empty volume resumes
# the previous login. The entrypoint wraps every command in Xvfb so Chrome is headed.
#   aggregator-bootstrap warm-sessions
#   aggregator-bootstrap login --channel noon   # headed reauth (now runnable on the VM)
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["warm-sessions"]

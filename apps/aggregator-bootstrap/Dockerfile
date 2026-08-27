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
COPY pyproject.toml ./
COPY src ./src

# Install the package (which brings the playwright python driver), then ONLY
# Google Chrome and its OS deps — not the firefox/webkit the all-browsers base
# carried. `--with-deps` is the apt half and needs root, which we still are here.
RUN pip install --no-cache-dir . \
 && playwright install --with-deps chrome \
 && rm -rf /var/lib/apt/lists/* /root/.cache

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

# Drop root: run as the unprivileged pwuser, exactly as the old image did.
USER pwuser

# Runs on a schedule (the /etc/cron.d Keeta pull). Hydrates Playwright state from
# the API first so a new image with an empty volume resumes the previous login.
#   aggregator-bootstrap warm-sessions
#   aggregator-bootstrap login --channel noon   # headed, once, from a laptop
ENTRYPOINT ["aggregator-bootstrap"]
CMD ["warm-sessions"]

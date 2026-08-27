# The browser-side worker. Runs as its own job, NOT with the API — this is the
# one place Playwright lives, kept off the app VM.
#
# Headed login on a laptop spawns branded Google Chrome as a normal OS process
# (Cloudflare sees a real window). The container only warms an already-captured
# profile; we still install Chrome so `channel="chrome"` works.
#
# `-noble` (Ubuntu 24.04) ships Python 3.12; the older `-jammy` ships 3.10, which
# this package's `requires-python = ">=3.12"` rejects at `pip install`.
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && playwright install chrome

# storage_state (the logged-in Playwright sessions) is persisted here between
# runs. It MUST be a mounted persistent volume in production — see the README
# "Operations" section — or every scheduled run starts logged-out. `pwuser`
# ships in the base image; the dir is created and chowned to it before we drop
# root so the non-root process can write the refreshed sessions.
RUN mkdir -p /data/sessions && chown -R pwuser:pwuser /data
VOLUME ["/data"]

# Drop root: run as the unprivileged `pwuser` the base image provides.
USER pwuser

# Runs on a schedule (a cron/K8s CronJob). Hydrates Playwright state from the
# API first so a new image with an empty volume resumes the previous login.
#   aggregator-bootstrap warm-sessions
#   aggregator-bootstrap login --channel noon   # headed, once, from a laptop
ENTRYPOINT ["aggregator-bootstrap"]
CMD ["warm-sessions"]

# The browser-side worker. Runs as its own job, NOT with the API — this is the
# one place Playwright lives, kept off the app VM.
#
# The base image already bundles the Playwright browsers (chromium included) at
# the exact version pinned below, so there is no separate `playwright install`
# step — re-running it would just re-download what the image already ships.
FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# storage_state (the logged-in Playwright sessions) is persisted here between
# runs. It MUST be a mounted persistent volume in production — see the README
# "Operations" section — or every scheduled run starts logged-out. `pwuser`
# ships in the base image; the dir is created and chowned to it before we drop
# root so the non-root process can write the refreshed sessions.
RUN mkdir -p /data/sessions && chown -R pwuser:pwuser /data
VOLUME ["/data"]

# Drop root: run as the unprivileged `pwuser` the base image provides.
USER pwuser

# Runs on a schedule (a cron/K8s CronJob), e.g.:
#   aggregator-bootstrap warm-sessions            # every few hours, all channels
#   aggregator-bootstrap capture-and-push --all   # a full re-capture
ENTRYPOINT ["aggregator-bootstrap"]
CMD ["warm-sessions"]

# The browser-side worker. Runs as its own job, NOT with the API — this is the
# one place Playwright lives, kept off the app VM.
FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && python -m playwright install chromium

# Runs on a schedule (a cron/K8s CronJob), e.g.:
#   aggregator-bootstrap warm-sessions            # every few hours, all channels
#   aggregator-bootstrap capture-and-push --all   # a full re-capture
ENTRYPOINT ["aggregator-bootstrap"]
CMD ["warm-sessions"]

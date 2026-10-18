FROM node:24-bookworm-slim AS node-runtime
FROM python:3.12-slim-bookworm
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY wheels /wheels
RUN node --version && python -m pip install --no-index --find-links=/wheels pytest==8.4.2

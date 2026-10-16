FROM python:3.12-slim
COPY wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels pytest==8.4.2

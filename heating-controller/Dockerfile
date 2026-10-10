FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml ./
COPY heating_controller ./heating_controller
RUN pip install --no-cache-dir . tzdata \
    && mkdir /data \
    && chown 1000:1000 /data

USER 1000:1000
ENTRYPOINT ["python", "-m", "heating_controller"]
CMD ["/config/controller.yaml", "--dry-run"]

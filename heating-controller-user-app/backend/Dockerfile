FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY heating_app ./heating_app
RUN pip install --no-cache-dir .
COPY gunicorn.conf.py ./
USER 1000:1000
CMD ["gunicorn", "-c", "gunicorn.conf.py", "--bind", "0.0.0.0:5055", "heating_app.wsgi:app"]

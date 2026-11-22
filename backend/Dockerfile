FROM python:3.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/cyberflow
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --user-group --create-home cyberflow \
    && install -d -m 0700 -o cyberflow -g cyberflow /var/lib/cyberflow/logs
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY alembic.ini backend/alembic.ini
COPY migrations/ backend/migrations/
COPY app/ backend/app/
USER cyberflow
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

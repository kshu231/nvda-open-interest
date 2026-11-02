# The live gamma exposure service (live_server.py) for Google Cloud Run.
FROM python:3.12-slim
WORKDIR /app
COPY nvda_oi.py nvda_gex.py schwab_api.py live_server.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "live_server.py"]

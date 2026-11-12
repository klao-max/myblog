FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATA_DIR=/data

COPY . /app
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser \
    && mkdir -p /data \
    && chown -R appuser:appgroup /app /data

VOLUME ["/data"]
EXPOSE 8000
USER appuser
CMD ["python", "app.py"]

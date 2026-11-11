FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATA_DIR=/data

COPY . /app
RUN mkdir -p /data

VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "app.py"]

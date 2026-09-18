# slim python; api and worker share the same image

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LAB_DATA_DESK_DB=/app/data/lab-data-desk.db \
    LAB_DATA_DESK_UPLOADS=/app/uploads

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin labdesk

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY sample_data ./sample_data

RUN mkdir -p /app/data /app/uploads \
    && chown -R labdesk:labdesk /app/data /app/uploads

USER labdesk

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

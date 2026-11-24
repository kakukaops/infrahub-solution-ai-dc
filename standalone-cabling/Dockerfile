FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py validator.py index.html ./

USER 10001:10001
EXPOSE 8080
CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8080", "--no-browser"]

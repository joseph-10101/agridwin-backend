FROM python:3.9-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

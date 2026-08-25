FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./
COPY scenarios ./scenarios

EXPOSE 8022
CMD ["python", "server.py"]

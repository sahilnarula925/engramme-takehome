FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY starter-kit/memkit /app/memkit
COPY service /app/service
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "service"]

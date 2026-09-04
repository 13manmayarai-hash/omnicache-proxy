FROM python:3.11-slim

WORKDIR /app

# Install lightweight system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install python runtime packages
RUN pip install --no-cache-dir starlette uvicorn httpx

# Copy source tree
COPY . /app

EXPOSE 8000

ENV OMNICACHE_PORT=8000
ENV OMNICACHE_HOST=0.0.0.0
ENV REQUIRE_AUTH=false

CMD ["python3", "main.py"]

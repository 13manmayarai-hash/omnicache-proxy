FROM python:3.11-slim

WORKDIR /app

# Install lightweight system dependencies (git for tool replay fingerprinting, curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*

# Copy source tree
COPY . /app

# Install omnicache and dependencies
RUN pip install --no-cache-dir .

EXPOSE 8000

ENV OMNICACHE_PORT=8000
ENV OMNICACHE_HOST=0.0.0.0
ENV REQUIRE_AUTH=false

CMD ["omnicache"]

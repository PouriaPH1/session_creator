FROM python:3.11-slim

WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source code
COPY . .

# Expose Web Admin Panel Port
EXPOSE 8000

# Volume for persisting session files and passwords
VOLUME ["/app/storage"]

# Run the master server
CMD ["python", "run_server.py", "--host", "0.0.0.0", "--port", "8000"]

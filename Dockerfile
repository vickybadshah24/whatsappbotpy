FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY *.py .
COPY .env.example .

# Create log directory
RUN mkdir -p /app/logs

EXPOSE 5000

# Use waitress on all platforms inside container
CMD ["python", "-m", "waitress", "--host=0.0.0.0", "--port=5000", "app:app"]

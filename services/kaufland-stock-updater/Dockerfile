FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy shared-db library
COPY libs/shared-db /app/libs/shared-db

# Install shared-db library
WORKDIR /app/libs/shared-db
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -e .

# Copy kaufland-rest-api library
WORKDIR /app
COPY libs/kaufland-rest-api /app/libs/kaufland-rest-api

# Install kaufland-rest-api library
WORKDIR /app/libs/kaufland-rest-api
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -e .

# Copy application files
WORKDIR /app
COPY services/kaufland-stock-updater /app/app

# Install application dependencies
WORKDIR /app/app
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.txt

# Set PYTHONPATH to include libs directories for imports
ENV PYTHONPATH=/app/libs/shared-db:/app/libs/kaufland-rest-api

# Set working directory to app
WORKDIR /app/app

# Run the application
CMD ["python", "main.py"]

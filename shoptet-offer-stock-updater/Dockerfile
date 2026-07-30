FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY shoptet-offer-stock-updater /app/app

WORKDIR /app/app
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.txt

CMD ["python", "main.py"]

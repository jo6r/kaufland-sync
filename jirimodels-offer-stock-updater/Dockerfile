FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY jirimodels-offer-stock-updater /app/app

WORKDIR /app/app
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.txt

WORKDIR /app/app

CMD ["python", "main.py"]
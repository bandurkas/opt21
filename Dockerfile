FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py analytics_collector.py db_schema.sql analytics_schema.sql ./
CMD ["python", "collector.py"]

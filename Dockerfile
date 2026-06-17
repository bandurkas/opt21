FROM python:3.10-slim
WORKDIR /app
COPY collector.py radar_bot.py h1_bot.py h2_bot.py db_schema.sql requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt pyTelegramBotAPI pandas numpy
CMD ["python", "collector.py"]

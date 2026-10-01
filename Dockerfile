FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ingest.py core.py db.py notify.py api.py run.sh ./
COPY public ./public
# Mount a volume at /data so water.db survives restarts.
ENV WATER_DB=/data/water.db PORT=8000
RUN mkdir /data && chmod +x run.sh
EXPOSE 8000
CMD ["./run.sh"]

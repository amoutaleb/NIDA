# NiDa — National Integrated Disaster Alert
# Production container image
FROM python:3.11-slim

WORKDIR /app

# System deps for shapely/scikit-learn wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY data ./data

# Render/Railway inject PORT; default 8000 for local docker run
ENV PORT=8000
EXPOSE 8000

CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}

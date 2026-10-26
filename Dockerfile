FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && python3 -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')" \
    && rm /tmp/requirements.txt

RUN apt update && apt install build-essential -y

COPY main.py ./main.py
CMD ["python3", "main.py"]
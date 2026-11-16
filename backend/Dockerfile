FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY app ./app
COPY seed.py ./seed.py
COPY seed_sla.py ./seed_sla.py

ENV DATABASE_URL=postgresql+psycopg2://ccdev:ccdev@db:5432/ccdev \
    SEED_DEMO=true \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

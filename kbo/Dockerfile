FROM python:3.11-slim

WORKDIR /kbo

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

COPY extract.py KboOpenData_0540_2026_11_10_Full.zip ./

ENTRYPOINT ["python", "main.py"]
FROM python:3.11-slim

WORKDIR /app

# تثبيت المتطلبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ الكود
COPY . .

# المنفذ
EXPOSE 8000

# تشغيل التطبيق
CMD ["python", "main.py"]

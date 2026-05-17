FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY . .

# Puerto
EXPOSE 3001

# Run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3001", "--workers", "2"]
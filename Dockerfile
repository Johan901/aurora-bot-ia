# Usa una imagen base con Python y Tesseract
FROM python:3.11-slim

# Instala tesseract
RUN apt-get update && apt-get install -y tesseract-ocr libglib2.0-0 libsm6 libxext6 libxrender-dev

# Establece el directorio de trabajo
WORKDIR /app

# Copia los archivos
COPY . .

# Instala dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Expone el puerto (Render usa 0.0.0.0:$PORT)
ENV PORT=5000
CMD ["python", "app.py"]

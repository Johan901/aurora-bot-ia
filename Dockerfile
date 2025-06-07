# Usa una imagen base con Python 3.11
FROM python:3.11-slim

# Evita preguntas interactivas
ENV DEBIAN_FRONTEND=noninteractive

# Actualiza sistema e instala dependencias necesarias para EasyOCR y OpenCV
RUN apt-get update && apt-get install -y \
    apt-utils \
    build-essential \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgl1-mesa-glx \
    libopencv-dev \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Establece el directorio de trabajo
WORKDIR /app

# Copia los archivos del proyecto al contenedor
COPY . .

# Instala las dependencias Python
RUN pip install --no-cache-dir -r requirements.txt

# Expone el puerto 5000
ENV PORT=5000

# Comando para ejecutar la app
CMD ["python", "app.py"]

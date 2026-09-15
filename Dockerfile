FROM python:3.11-slim

# Instalar Node.js 20 (necesario para gmgn-cli)
RUN apt-get update && apt-get install -y curl
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
RUN apt-get install -y nodejs

# Instalar gmgn-cli globalmente
RUN npm install -g gmgn-cli

# Configurar directorio de trabajo
WORKDIR /app

# Copiar e instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Exponer el puerto de Render
EXPOSE 8000

# Comando de arranque (Render inyecta la variable $PORT)
CMD ["sh", "-c", "gunicorn backend:app -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT"]

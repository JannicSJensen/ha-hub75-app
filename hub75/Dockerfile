FROM python:3.13-alpine

RUN apk add --no-cache ffmpeg nodejs \
	&& node -e 'if (Number(process.versions.node.split(".")[0]) < 22) process.exit(1)'

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
CMD ["python3", "/app/app.py"]
# Radal backend — FastAPI on AWS Lambda via Lambda Web Adapter (LWA)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Chromium for the Playwright PDF pipeline (Bases Técnicas / comparison / propuesta).
# Install to a fixed, world-readable path (NOT /root/.cache, which depends on HOME)
# and export it so the runtime resolves the same browser. --with-deps pulls the
# system libs (libnss3, fonts, etc.). On Lambda, pdf.py adds --single-process/
# --no-zygote and a /tmp cache so this Chromium can launch in the read-only sandbox.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium

COPY app ./app

# Lambda Web Adapter (proxies Lambda Runtime API -> our uvicorn server)
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.7.0 /lambda-adapter /opt/extensions/lambda-adapter
ENV PORT=8000
ENV AWS_LWA_INVOKE_MODE=buffered
ENV AWS_LWA_READINESS_CHECK_PORT=8000
ENV AWS_LWA_READINESS_CHECK_PATH=/api/v1/health

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]

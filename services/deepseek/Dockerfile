# syntax=docker/dockerfile:1
FROM python:3.12-slim

ARG DEEPSEEK_API_REPOSITORY=https://github.com/smkttl/deepseek-api.git
ARG DEEPSEEK_API_COMMIT=a19ee7e7987c56eb6334c10aff4db74421130bac

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/deepseek-api
COPY apply_upstream_patch.py /tmp/apply_upstream_patch.py
RUN git clone "${DEEPSEEK_API_REPOSITORY}" . \
    && git checkout "${DEEPSEEK_API_COMMIT}" \
    && python /tmp/apply_upstream_patch.py DeepSeekAPI/DeepSeekChat/main.py \
    && pip install --no-cache-dir -r requirements.txt requests curl_cffi \
    && rm -rf .git /tmp/apply_upstream_patch.py

COPY stateful_server.py ./stateful_server.py

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["python", "stateful_server.py", "--host", "0.0.0.0", "--port", "8000"]

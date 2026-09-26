# Macroplastic web service (API + map UI), CPU only, linux/amd64 (verification status: reports/selfcheck/docker_check.md).
#   docker compose up --build -d   ->   http://localhost:8070
# Frontend: prebuilt service/static_v2 from git (no npm). Weights: weights/ from git (nothing is downloaded at start).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src:/app \
    CUDA_VISIBLE_DEVICES="" \
    MPLBACKEND=Agg

# libgomp1: OpenMP runtime for lightgbm / torch CPU
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Exact versions from requirements-lock.txt, PyTorch switched from the CUDA 12.8 wheels to the CPU wheels.
COPY requirements-lock.txt ./
RUN sed -e 's#whl/cu128#whl/cpu#' -e 's#+cu128#+cpu#' requirements-lock.txt > /tmp/req-cpu.txt \
 && pip install -r /tmp/req-cpu.txt \
 && rm /tmp/req-cpu.txt

COPY . .

EXPOSE 8070
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8070/api/v3/meta', timeout=8).status == 200 else 1)"

CMD ["python", "-m", "service", "--host", "0.0.0.0", "--port", "8070"]

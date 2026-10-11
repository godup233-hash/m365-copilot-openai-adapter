FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 只复制安装所需文件；.env / token cache 永不进镜像
COPY pyproject.toml README.md ./
COPY app ./app
COPY cli ./cli
RUN pip install .

# 非 root 运行
RUN groupadd -r adapter && useradd -r -g adapter -d /app adapter \
    && mkdir -p /data && chown -R adapter:adapter /data /app
USER adapter

# 容器内默认：token cache 落在持久化卷 /data；监听全网卡（默认只绑回环的是本地开发模式）
ENV TOKEN_CACHE_PATH=/data/token_cache.bin \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["m365-adapter", "serve"]

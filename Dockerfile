FROM python:3.11-slim

LABEL description="A股选股助手 - 价值+技术+估值分位综合打分"

WORKDIR /app

# 先装依赖（利用 Docker 缓存层）
# 使用清华镜像加速，lxml/pandas 等均为预编译 wheel，无需 apt 装编译器
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制应用代码
COPY . .

# baostock 缓存目录
RUN mkdir -p /app/.baostock_cache

EXPOSE 8501

# Streamlit 配置：允许外部访问
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501')" || exit 1

CMD ["streamlit", "run", "app.py"]

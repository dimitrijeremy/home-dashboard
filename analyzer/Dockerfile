FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1-mesa-glx libsm6 libxext6 libxrender-dev gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install torch CPU-only (arm64-native, much lighter than CUDA build)
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-u", "analyzer.py"]

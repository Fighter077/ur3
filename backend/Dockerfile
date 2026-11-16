FROM python:3.12-slim

# Laufzeitabhängigkeiten:
#   ffmpeg            -> aiortc/av
#   libgl1 libglib2.0 -> opencv
#   libegl1 libgles2  -> MediaPipe Tasks (HandLandmarker braucht libEGL.so.1, auch im CPU-Modus)
#   tesseract-ocr     -> OCR (pytesseract)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 libegl1 libgles2 tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# CPU-only Torch ZUERST aus dem PyTorch-CPU-Index -> ultralytics zieht dann nicht den
# mehrere GB großen CUDA-Stack (nvidia-cu*). Für GPU-Betrieb diesen Schritt entfernen und
# YOLO_DEVICE=cuda setzen (dann installiert ultralytics CUDA-Torch automatisch).
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt \
 # ultralytics zieht opencv-python, mediapipe opencv-contrib-python -> zwei konkurrierende cv2.
 # Beide entfernen und durch EINE headless-Contrib-Variante ersetzen (enthält alle Module,
 # braucht kein libGL, erfüllt sowohl ultralytics als auch mediapipe zur Laufzeit).
 && pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless opencv-contrib-python-headless 2>/dev/null; \
    pip install --no-cache-dir "opencv-contrib-python-headless<5"

COPY . .

EXPOSE 8080
CMD ["python", "server.py"]

"""YOLO-Wrapper. 2D-Detektion/Tracking auf einem BGR-Frame (OpenCV).

Dasselbe Modul bedient zwei Modelltypen:
  * Objekt-Modell  (z. B. yolo11n.pt)      -> Boxen pro Klasse
  * Pose-Modell    (z. B. yolo11n-pose.pt) -> Personen + 17 COCO-Keypoints (Körper-Skelett)

Ausgaben sind normalisiert (0..1), damit die Kamera-Auflösung die nachgelagerte Logik nicht
betrifft. Die 3D-/Tiefen-Schätzung liegt bewusst NICHT hier (siehe handover/Roboterkinematik),
damit später eine Tiefenkamera eingehängt werden kann, ohne den Detektor zu ändern.

WICHTIG: Finger-Landmarks liefert YOLO-Pose NICHT (nur das Handgelenk). Dafür gibt es ein
separates Modul (`hands.py`, MediaPipe).
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from config import config


class Detector:
    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or config.YOLO_MODEL_PATH
        self.enabled = True
        try:
            from ultralytics import YOLO
        except ImportError:
            print("[perception] ultralytics nicht installiert -> Detektion deaktiviert.")
            self.model = None
            self.enabled = False
            return

        try:
            self.model = YOLO(self.model_path)
            # Warmup
            dummy = np.zeros((640, 640, 3), dtype=np.uint8)
            self.model.predict(dummy, device=config.YOLO_DEVICE, verbose=False)
            print(f"[perception] YOLO geladen: {self.model_path} (device={config.YOLO_DEVICE})")
        except Exception as e:
            print(f"[perception] YOLO ({self.model_path}) konnte nicht geladen werden: {e} -> deaktiviert.")
            self.model = None
            self.enabled = False

    def detect(self, frame_bgr, *, track: bool = True) -> List[dict]:
        """Liste normalisierter Detektionen:
        {class, confidence, bbox:[x1,y1,x2,y2] (0..1), cx, cy, id?, keypoints?}

        keypoints (nur Pose-Modell): Liste von [x, y, conf] (x,y in 0..1), in COCO-Reihenfolge
        (0 Nase, 5/6 Schultern, 7/8 Ellbogen, 9/10 Handgelenke, 11/12 Hüften, 13/14 Knie, ...).
        """
        if not self.enabled or self.model is None:
            return []

        h, w = frame_bgr.shape[:2]
        # tracker="bytetrack.yaml": ByteTrack nutzt KEINEN optischen Fluss. BoT-SORT (Default)
        # macht Bewegungskompensation per Lucas-Kanade und stürzt ab, sobald zwei Frames
        # unterschiedlich groß sind - genau das passiert bei WebRTC mit adaptiver Auflösung.
        infer = self.model.track if track else self.model.predict
        tracking = {"persist": True, "tracker": "bytetrack.yaml"} if track else {}
        results = infer(
            frame_bgr, conf=config.YOLO_CONF, iou=config.YOLO_IOU,
            device=config.YOLO_DEVICE, verbose=False, **tracking,
        )
        out: List[dict] = []
        for res in results:
            boxes = res.boxes
            if boxes is None:
                continue
            names = res.names
            ids = boxes.id
            kps = getattr(res, "keypoints", None)
            kp_xy = kps.xy if (kps is not None and kps.xy is not None) else None
            kp_conf = kps.conf if (kps is not None and getattr(kps, "conf", None) is not None) else None
            for i in range(len(boxes)):
                x1, y1, x2, y2 = (float(v) for v in boxes.xyxy[i])
                det = {
                    "class": names[int(boxes.cls[i])],
                    "confidence": float(boxes.conf[i]),
                    "bbox": [x1 / w, y1 / h, x2 / w, y2 / h],
                    "cx": (x1 + x2) / 2 / w,
                    "cy": (y1 + y2) / 2 / h,
                }
                if ids is not None:
                    det["id"] = int(ids[i])
                if kp_xy is not None and i < len(kp_xy):
                    pts = kp_xy[i]
                    confs = kp_conf[i] if kp_conf is not None else None
                    det["keypoints"] = [
                        [float(pts[j][0]) / w, float(pts[j][1]) / h,
                         float(confs[j]) if confs is not None else 1.0]
                        for j in range(len(pts))
                    ]
                out.append(det)
        return out

"""Marker-based handover geometry in the UR base frame."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from .multiview_pose import _array, estimate_pose


class HandoverGeometry:
    def __init__(self, config: dict):
        self.cameras = [config["local_camera"], config["remote_camera"]]
        for camera in self.cameras:
            width, height = _array(camera["image_size_px"], "image_size_px", (2,))
            if width < 1 or height < 1 or width != int(width) or height != int(height):
                raise ValueError("image_size_px must give the calibrated width and height")
            intrinsics = _array(camera["intrinsics"], "intrinsics", (3, 3))
            if intrinsics[0, 0] <= 0 or intrinsics[1, 1] <= 0:
                raise ValueError("Camera focal lengths must be positive")
            distortion = np.asarray(camera["distortion"], dtype=float)
            if distortion.shape not in {(4,), (5,), (8,), (12,), (14,)} or not np.isfinite(distortion).all():
                raise ValueError("Invalid camera distortion coefficients")
            _array(camera["base_to_camera_rvec"], "base_to_camera_rvec", (3,))
            _array(camera["base_to_camera_translation_m"], "base_to_camera_translation_m", (3,))
        self.marker_id = config["marker_id"]
        if type(self.marker_id) is not int or not 0 <= self.marker_id < 50:
            raise ValueError("marker_id must be an integer from 0 to 49")
        size = float(config["marker_size_m"])
        self.approach_distance = float(config["approach_distance_m"])
        if not np.isfinite([size, self.approach_distance]).all() or min(size, self.approach_distance) <= 0:
            raise ValueError("Marker size and approach distance must be positive")
        self.points = [[0, 0, 0], [size, 0, 0], [size, size, 0], [0, size, 0]]
        self.offset = _array(config["tcp_in_marker_translation_m"], "tcp_in_marker_translation_m", (3,))
        offset_rvec = _array(config["tcp_in_marker_rvec"], "tcp_in_marker_rvec", (3,))
        self.orientation, _ = cv2.Rodrigues(offset_rvec)
        self.workspace_min = _array(config["workspace_min_m"], "workspace_min_m", (3,))
        self.workspace_max = _array(config["workspace_max_m"], "workspace_max_m", (3,))
        if np.any(self.workspace_min >= self.workspace_max):
            raise ValueError("Invalid mock workspace bounds")
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self.detector = cv2.aruco.ArucoDetector(dictionary, params)

    def detect(self, image, frame_size: tuple[int, int]):
        corners, ids, _ = self.detector.detectMarkers(image)
        if ids is None:
            return None
        matches = np.flatnonzero(ids.ravel() == self.marker_id)
        if len(matches) != 1:
            return None
        height, width = image.shape[:2]
        pixels = (corners[matches[0]].reshape(4, 2) + 0.5) * (
            np.asarray(frame_size) / (width, height)
        ) - 0.5
        return frame_size, pixels.tolist()

    def estimate(self, local, remote):
        cameras = []
        for camera, (size, pixels) in zip(self.cameras, (local, remote)):
            if tuple(camera["image_size_px"]) != size:
                return None
            cameras.append({**camera, "image_points_px": pixels})
        try:
            result = estimate_pose({"target_points_m": self.points, "cameras": cameras})
        except (ValueError, cv2.error, np.linalg.LinAlgError):
            return None
        if result["min_triangulation_angle_deg"] < 3 or any(
            error["max_px"] > 3 for error in result["reprojection_error"]
        ):
            return None
        rotation, _ = cv2.Rodrigues(np.asarray(result["target_to_base_rvec"]))
        tcp = np.asarray(result["target_to_base_translation_m"]) + rotation @ self.offset
        tool_rotation = rotation @ self.orientation
        approach = tcp - self.approach_distance * tool_rotation[:, 2]
        if any(np.any(point < self.workspace_min) or np.any(point > self.workspace_max) for point in (approach, tcp)):
            return None
        tool_rvec, _ = cv2.Rodrigues(tool_rotation)
        return tuple(np.r_[approach, tool_rvec.ravel()].tolist()), tuple(np.r_[tcp, tool_rvec.ravel()].tolist())


def load_handover_geometry(data_dir: str) -> HandoverGeometry | None:
    path = Path(data_dir) / "handover_calibration.json"
    try:
        return HandoverGeometry(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, cv2.error) as exc:
        print(f"[handover] Invalid calibration {path}: {exc}", flush=True)
        return None

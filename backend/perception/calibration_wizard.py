"""ChArUco calibration for the two fixed handover cameras."""
from __future__ import annotations

import cv2
import numpy as np

from .handover_geometry import HandoverGeometry
from .multiview_pose import _array

MIN_CAPTURES = 8
_MAX_CAPTURES = 24
_MAX_ERROR_PX = 2.0
_GRASP_FIELDS = (
    "marker_id", "marker_size_m", "tcp_in_marker_translation_m", "tcp_in_marker_rvec",
    "approach_distance_m", "workspace_min_m", "workspace_max_m",
)


def calibration_board(square_size_m: float):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    return cv2.aruco.CharucoBoard((7, 5), square_size_m, 0.7 * square_size_m, dictionary)


def board_image() -> bytes:
    image = calibration_board(0.03).generateImage((840, 640), marginSize=70)
    cv2.putText(image, "Origin", (20, 53), cv2.FONT_HERSHEY_SIMPLEX, 0.65, 0, 2)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Could not generate calibration board.")
    return encoded.tobytes()


class CalibrationWizard:
    def __init__(self, square_size_m: float):
        if not np.isfinite(square_size_m) or square_size_m <= 0:
            raise ValueError("Enter the measured square size in metres.")
        self.board = calibration_board(square_size_m)
        self.detector = cv2.aruco.CharucoDetector(self.board)
        self.samples = []
        self.source_ids = None
        self.reference = None

    def _detect(self, frame):
        corners, ids, _, _ = self.detector.detectBoard(frame.to_ndarray(format="gray"))
        if ids is None or len(ids) < 12 or self.board.checkCharucoCornersCollinear(ids):
            raise ValueError("Show more of the ChArUco board in both cameras.")
        points, pixels = self.board.matchImagePoints(corners, ids)
        return (frame.width, frame.height), points, pixels, ids.ravel()

    def _pair(self, frames, source_ids):
        if self.source_ids is not None and self.source_ids != source_ids:
            raise ValueError("A camera has reconnected. Restart the wizard with both cameras fixed.")
        pair = tuple(self._detect(frame) for frame in frames)
        if self.samples and any(new[0] != old[0] for new, old in zip(pair, self.samples[0])):
            raise ValueError("A camera changed resolution. Restart the wizard at the operating resolution.")
        return pair

    def capture(self, frames, source_ids):
        if len(self.samples) >= _MAX_CAPTURES:
            raise ValueError("Capture limit reached. Continue to the reference pose or restart.")
        pair = self._pair(frames, source_ids)
        for current, previous in zip(pair, zip(*self.samples) if self.samples else ((), ())):
            _, _, pixels, ids = current
            for size, _, old_pixels, old_ids in previous:
                common, i, j = np.intersect1d(ids, old_ids, return_indices=True)
                if len(common) >= 8 and np.mean(np.linalg.norm(
                    pixels.reshape(-1, 2)[i] - old_pixels.reshape(-1, 2)[j], axis=1,
                )) < min(size) * 0.015:
                    raise ValueError("That view is too similar to an earlier capture. Move and tilt the board.")
        self.source_ids = source_ids
        self.samples.append(pair)
        self.reference = None
        return len(self.samples)

    def capture_reference(self, frames, source_ids, board_in_base_pose):
        if len(self.samples) < MIN_CAPTURES:
            raise ValueError(f"Capture at least {MIN_CAPTURES} board views first.")
        pose = _array(board_in_base_pose, "Board pose", (6,))
        self.reference = (self._pair(frames, source_ids), pose)

    def solve(self, source_ids, settings: dict):
        if self.reference is None or self.source_ids != source_ids:
            raise ValueError("Capture the reference pose with the same pair of cameras before saving.")
        reference_pair, board_pose = self.reference
        cameras = {}
        errors = {}
        for name, samples, reference in zip(("local_camera", "remote_camera"), zip(*self.samples), reference_pair):
            cameras[name], errors[name] = _calibrate_view(samples, reference, board_pose)
        centers = []
        for camera in cameras.values():
            rotation, _ = cv2.Rodrigues(np.asarray(camera["base_to_camera_rvec"]))
            centers.append(-rotation.T @ np.asarray(camera["base_to_camera_translation_m"]))
        if np.linalg.norm(centers[0] - centers[1]) < 1e-6:
            raise ValueError("The fitted camera positions coincide. Check the camera pair and reference capture.")
        config = {**cameras, **{key: settings[key] for key in _GRASP_FIELDS}}
        return config, HandoverGeometry(config), errors


def _calibrate_view(samples, reference, board_pose):
    size, object_points, pixels, _ = reference
    rms, intrinsics, distortion, _, _ = cv2.calibrateCamera(
        [sample[1] for sample in samples], [sample[2] for sample in samples], size, None, None,
    )
    ok, board_rvec, board_translation = cv2.solvePnP(object_points, pixels, intrinsics, distortion)
    if not ok:
        raise ValueError("Could not estimate the camera pose from the reference view.")
    projected, _ = cv2.projectPoints(object_points, board_rvec, board_translation, intrinsics, distortion)
    reference_rms = float(np.sqrt(np.mean(np.sum(
        (projected.reshape(-1, 2) - pixels.reshape(-1, 2)) ** 2, axis=1,
    ))))
    if not np.isfinite([rms, reference_rms]).all() or max(rms, reference_rms) > _MAX_ERROR_PX:
        raise ValueError(f"High reprojection error ({rms:.2f} px calibration, {reference_rms:.2f} px reference). Retake the views.")
    board_to_camera, _ = cv2.Rodrigues(board_rvec)
    board_to_base, _ = cv2.Rodrigues(board_pose[3:])
    base_to_camera = board_to_camera @ board_to_base.T
    base_to_camera_translation = board_translation.ravel() - base_to_camera @ board_pose[:3]
    base_rvec, _ = cv2.Rodrigues(base_to_camera)
    return {
        "image_size_px": list(size),
        "intrinsics": intrinsics.tolist(),
        "distortion": distortion.ravel().tolist(),
        "base_to_camera_rvec": base_rvec.ravel().tolist(),
        "base_to_camera_translation_m": base_to_camera_translation.tolist(),
    }, {"intrinsics_rms_px": float(rms), "reference_rms_px": reference_rms}

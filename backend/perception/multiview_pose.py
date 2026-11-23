"""Offline target-pose estimation from two calibrated, fixed cameras."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def _array(value, name: str, shape: tuple[int, ...]) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError(f"{name} must contain finite numbers with shape {shape}")
    return array


def estimate_pose(data: dict) -> dict:
    points = np.asarray(data["target_points_m"], dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 4 or not np.isfinite(points).all():
        raise ValueError("Provide at least four corresponding 3D target points in metres")
    centered_target = points - points.mean(axis=0)
    if np.linalg.matrix_rank(centered_target) < 2:
        raise ValueError("Target points must not be collinear")
    if len(data["cameras"]) != 2:
        raise ValueError("Two fixed cameras are required")

    projections, undistorted, centers, cameras = [], [], [], []
    for camera in data["cameras"]:
        intrinsics = _array(camera["intrinsics"], "intrinsics", (3, 3))
        if intrinsics[0, 0] <= 0 or intrinsics[1, 1] <= 0:
            raise ValueError("Camera focal lengths must be positive")
        distortion = np.asarray(camera["distortion"], dtype=np.float64)
        if distortion.shape not in {(4,), (5,), (8,), (12,), (14,)} or not np.isfinite(distortion).all():
            raise ValueError("Provide OpenCV distortion coefficients for each camera")
        rvec = _array(camera["base_to_camera_rvec"], "base_to_camera_rvec", (3,))
        translation = _array(camera["base_to_camera_translation_m"], "base_to_camera_translation_m", (3,))
        pixels = _array(camera["image_points_px"], "image_points_px", (len(points), 2))
        rotation, _ = cv2.Rodrigues(rvec)
        projections.append(np.column_stack((rotation, translation)))
        centers.append(-rotation.T @ translation)
        undistorted.append(cv2.undistortPoints(pixels, intrinsics, distortion).reshape(-1, 2).T)
        cameras.append((intrinsics, distortion, rvec, translation, pixels))

    if np.linalg.norm(centers[0] - centers[1]) < 1e-9:
        raise ValueError("Camera centers must be distinct")
    homogeneous = cv2.triangulatePoints(*projections, *undistorted)
    if not np.isfinite(homogeneous).all() or np.any(np.abs(homogeneous[3]) < 1e-10):
        raise ValueError("The views do not yield finite 3D points")
    reconstructed = (homogeneous[:3] / homogeneous[3]).T

    # Rigid registration places the known target geometry in the robot-base frame.
    centered_reconstruction = reconstructed - reconstructed.mean(axis=0)
    u, singular, vt = np.linalg.svd(centered_target.T @ centered_reconstruction)
    if singular[1] < singular[0] * 1e-8 or singular[0] < 1e-15:
        raise ValueError("The reconstructed points do not constrain a rigid pose")
    correction = np.diag([1, 1, np.sign(np.linalg.det(vt.T @ u.T))])
    rotation = vt.T @ correction @ u.T
    translation = reconstructed.mean(axis=0) - rotation @ points.mean(axis=0)
    fitted = points @ rotation.T + translation
    rvec, _ = cv2.Rodrigues(rotation)

    errors = []
    for projection, (intrinsics, distortion, camera_rvec, camera_translation, pixels) in zip(projections, cameras):
        if any(np.any(cloud @ projection[2, :3] + projection[2, 3] <= 0) for cloud in (reconstructed, fitted)):
            raise ValueError("Target points must lie in front of both cameras")
        projected, _ = cv2.projectPoints(fitted, camera_rvec, camera_translation, intrinsics, distortion)
        distance = np.linalg.norm(projected.reshape(-1, 2) - pixels, axis=1)
        errors.append({"rms_px": float(np.sqrt(np.mean(distance**2))), "max_px": float(np.max(distance))})

    rays = [reconstructed - center for center in centers]
    unit_rays = [ray / np.linalg.norm(ray, axis=1, keepdims=True) for ray in rays]
    cosines = np.sum(unit_rays[0] * unit_rays[1], axis=1)
    angles = np.rad2deg(np.arccos(np.clip(np.abs(cosines), 0.0, 1.0)))
    return {
        "target_to_base_translation_m": translation.tolist(),
        "target_to_base_rvec": rvec.ravel().tolist(),
        "reprojection_error": errors,
        "min_triangulation_angle_deg": float(np.min(angles)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path, help="JSON file with calibrated camera observations")
    args = parser.parse_args()
    try:
        result = estimate_pose(json.loads(args.observations.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, cv2.error, np.linalg.LinAlgError) as exc:
        parser.exit(1, f"Pose estimation failed: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

"""Synthetic metric pose recovery with two fixed cameras."""
from importlib.util import find_spec
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "perception"))

VISION_AVAILABLE = find_spec("numpy") is not None and find_spec("cv2") is not None


@unittest.skipUnless(VISION_AVAILABLE, "NumPy and OpenCV are required")
class MultiviewPoseTests(unittest.TestCase):
    def test_recovers_pose_and_rejects_coincident_camera_centers(self):
        import cv2
        import numpy as np
        from multiview_pose import estimate_pose

        points = np.array([[-.03, -.06, 0], [.03, -.06, 0], [.03, .06, 0], [-.03, .06, 0]])
        rvec = np.array([.12, -.2, .07])
        rotation, _ = cv2.Rodrigues(rvec)
        translation = np.array([.08, .02, .65])
        base_points = points @ rotation.T + translation
        intrinsics = np.array([[810., 0, 640.], [0, 790., 360.], [0, 0, 1.]])
        cameras = []
        for offset in (0., -.18):
            camera_rvec = np.array([0., offset, 0.])
            camera_translation = np.array([offset, 0., 0.])
            distortion = np.array([.02, -.01, .001, 0., 0.])
            projected, _ = cv2.projectPoints(base_points, camera_rvec, camera_translation, intrinsics, distortion)
            cameras.append({
                "intrinsics": intrinsics.tolist(),
                "distortion": distortion.tolist(),
                "base_to_camera_rvec": camera_rvec.tolist(),
                "base_to_camera_translation_m": camera_translation.tolist(),
                "image_points_px": projected.reshape(-1, 2).tolist(),
            })
        data = {"target_points_m": points.tolist(), "cameras": cameras}
        result = estimate_pose(data)
        np.testing.assert_allclose(result["target_to_base_translation_m"], translation, atol=1e-7)
        np.testing.assert_allclose(result["target_to_base_rvec"], rvec, atol=1e-7)
        self.assertLess(max(error["rms_px"] for error in result["reprojection_error"]), 1e-5)
        self.assertGreater(result["min_triangulation_angle_deg"], 1.0)

        data["cameras"][1] = cameras[0]
        with self.assertRaisesRegex(ValueError, "Camera centers must be distinct"):
            estimate_pose(data)

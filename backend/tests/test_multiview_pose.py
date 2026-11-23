"""Synthetic metric pose recovery with two fixed cameras."""
from importlib.util import find_spec
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))

VISION_AVAILABLE = find_spec("numpy") is not None and find_spec("cv2") is not None


@unittest.skipUnless(VISION_AVAILABLE, "NumPy and OpenCV are required")
class MultiviewPoseTests(unittest.TestCase):
    def test_calibration_registers_the_camera_in_robot_base_coordinates(self):
        import cv2
        import numpy as np
        from perception.calibration_wizard import _calibrate_view

        points = np.array([[x * .03, y * .03, 0] for y in range(1, 5) for x in range(1, 7)], np.float32)
        size = (1280, 720)
        intrinsics = np.array([[810., 0., 640.], [0., 790., 360.], [0., 0., 1.]])
        distortion = np.zeros(5)
        ids = np.arange(len(points))
        samples = []
        for i in range(9):
            rvec = np.array([.12 + .05 * i, .24 - .048 * i, (-1)**i * .12])
            shift = np.array([-.13 + .02 * (i % 3), -.09 + .025 * (i // 3), .48 + .025 * i])
            pixels, _ = cv2.projectPoints(points, rvec, shift, intrinsics, distortion)
            samples.append((size, points, pixels, ids))
        board_pose = np.array([.04, -.08, .58, .16, .08, -.15])
        expected_rvec = np.array([-.09, .13, .05])
        expected_offset = np.array([-.14, .01, .06])
        board_to_base, _ = cv2.Rodrigues(board_pose[3:])
        pixels, _ = cv2.projectPoints(
            points @ board_to_base.T + board_pose[:3],
            expected_rvec, expected_offset, intrinsics, distortion,
        )
        result, errors = _calibrate_view(samples, (size, points, pixels, ids), board_pose)
        np.testing.assert_allclose(result["base_to_camera_rvec"], expected_rvec, atol=1e-3)
        np.testing.assert_allclose(result["base_to_camera_translation_m"], expected_offset, atol=1e-3)
        self.assertLess(errors["reference_rms_px"], 0.01)

    def test_recovers_pose_and_rejects_coincident_camera_centers(self):
        import cv2
        import numpy as np
        from perception.multiview_pose import estimate_pose

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

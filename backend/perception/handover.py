"""Two-view presence check for the simulated handover."""
from __future__ import annotations

import math
from time import monotonic

_PHONE_CLASSES = {"cell phone", "smartphone", "mobile phone"}
_STABLE_SAMPLES = 3
_MAX_PAIR_SKEW_S = 0.2
_MAX_GRASP_AGE_S = 0.6


def same_grasp(first, second) -> bool:
    if math.dist(first[:3], second[:3]) > 0.008:
        return False

    def quaternion(rvec):
        angle = math.hypot(*rvec)
        scale = math.sin(angle / 2) / angle if angle else 0.5
        return (math.cos(angle / 2), *(component * scale for component in rvec))

    cosine = abs(sum(a * b for a, b in zip(quaternion(first[3:]), quaternion(second[3:]))))
    return cosine >= math.cos(math.radians(5) / 2)


class GraspTracking:
    def __init__(self, geometry):
        self.geometry = geometry
        self.markers = {}
        self.last_pair = (0.0, 0.0)
        self.reference = None
        self.estimate = None

    def update(self, network, marker, timestamp):
        if marker is None:
            self.markers.pop(network, None)
            self.reference = self.estimate = None
            return
        self.markers[network] = (timestamp, marker)
        if len(self.markers) != 2:
            return
        local, remote = self.markers[False], self.markers[True]
        stamps = (local[0], remote[0])
        if any(new <= old for new, old in zip(stamps, self.last_pair)):
            return
        if abs(stamps[0] - stamps[1]) > _MAX_PAIR_SKEW_S:
            self.reference = self.estimate = None
            return
        self.last_pair = stamps
        plan = self.geometry.estimate(local[1], remote[1])
        if plan is None or monotonic() - min(stamps) > _MAX_GRASP_AGE_S:
            self.reference = self.estimate = None
            return
        if (
            self.estimate is not None and min(stamps) - self.estimate[0] <= _MAX_GRASP_AGE_S
            and same_grasp(plan[1], self.reference)
        ):
            count = self.estimate[2] + 1
        else:
            self.reference = plan[1]
            count = 1
        self.estimate = (min(stamps), plan, count)

    def candidate(self):
        estimate = self.estimate
        if estimate is None or estimate[2] < _STABLE_SAMPLES or monotonic() - estimate[0] > _MAX_GRASP_AGE_S:
            return None
        return estimate[1]


class HandoverObservation:
    def __init__(self, geometry=None):
        self.tracking = GraspTracking(geometry) if geometry is not None else None

    def update(self, network: bool, objects: list, *, captured_at: float, marker=None) -> dict:
        targets = [box for box in objects if box["class"].strip().lower() in _PHONE_CLASSES]
        if self.tracking is not None:
            self.tracking.update(network, marker, captured_at)
        plan = self.grasp_candidate()
        return {
            "objects": targets,
            "ready": plan is not None,
            "calibrated": self.tracking is not None,
            "grasp_tcp_m": plan[1][:3] if plan else None,
        }

    def grasp_candidate(self):
        return self.tracking.candidate() if self.tracking is not None else None

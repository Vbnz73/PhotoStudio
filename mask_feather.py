"""Local, non-destructive feathering for grayscale foreground masks."""

from math import ceil
from typing import Optional, Tuple

import cv2
import numpy as np


Rect = Tuple[int, int, int, int]


class MaskFeather:
    """Cache edge distances and derive feathered masks without changing input.

    Soft alpha values supplied by brushes are kept exactly as they are. The
    distance transform is used only to locate hard (0/255) pixels near a
    contour; it is never used as a replacement for the editable mask.
    """

    def __init__(self, max_width_fraction: float = 0.005):
        self.max_width_fraction = max(0.0, float(max_width_fraction))
        self.reset()

    def reset(self) -> None:
        self._shape = None
        self._max_radius = 0.0
        self._indices = np.empty(0, dtype=np.int64)
        self._distances = np.empty(0, dtype=np.float32)
        self._protected_exclusion = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @staticmethod
    def _validate_mask(mask: np.ndarray) -> np.ndarray:
        result = np.asarray(mask)
        if result.ndim != 2:
            raise ValueError("MaskFeather expects a two-dimensional mask")
        if result.dtype != np.uint8:
            result = np.clip(result, 0, 255).astype(np.uint8)
        return np.ascontiguousarray(result)

    def _radius_for_shape(self, shape) -> float:
        height, width = shape
        return self.max_width_fraction * min(height, width)

    @staticmethod
    def _signed_distance(binary: np.ndarray) -> Optional[np.ndarray]:
        if not np.any(binary) or np.all(binary):
            return None

        inside = cv2.distanceTransform(
            binary, cv2.DIST_L2, cv2.DIST_MASK_3
        )
        outside = cv2.distanceTransform(
            1 - binary, cv2.DIST_L2, cv2.DIST_MASK_3
        )

        # Distance-transform values measure between pixel centers. Shifting
        # by half a pixel places the zero contour between foreground/background
        # pixel centers and keeps the 50% alpha contour on the original edge.
        signed = np.empty(binary.shape, dtype=np.float32)
        foreground = binary != 0
        signed[foreground] = inside[foreground] - 0.5
        signed[~foreground] = -(outside[~foreground] - 0.5)
        return signed

    @staticmethod
    def _make_protected_exclusion(mask_shape, protected_mask, radius: float):
        if protected_mask is None:
            return None
        protected = np.asarray(protected_mask)
        if protected.shape != mask_shape:
            return None

        protected = (protected > 0).astype(np.uint8)
        if not np.any(protected):
            return None

        pad = max(1, int(ceil(radius)) + 1)
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (pad * 2 + 1, pad * 2 + 1),
        )
        return cv2.dilate(protected, kernel) > 0

    @staticmethod
    def _clip_rect(rect: Rect, width: int, height: int) -> Rect:
        x, y, rect_width, rect_height = rect
        left = max(0, min(width, int(x)))
        top = max(0, min(height, int(y)))
        right = max(left, min(width, int(x + rect_width)))
        bottom = max(top, min(height, int(y + rect_height)))
        return left, top, right, bottom

    def _make_binary(self, mask: np.ndarray, protected_mask) -> np.ndarray:
        binary = (mask >= 128).astype(np.uint8)
        if protected_mask is not None:
            protected = np.asarray(protected_mask)
            if protected.shape == mask.shape:
                binary[protected > 0] = 0
        return binary

    @staticmethod
    def _samples_for_region(
        mask: np.ndarray,
        signed_distance: Optional[np.ndarray],
        exclusion: Optional[np.ndarray],
        max_radius: float,
        x_offset: int,
        y_offset: int,
        full_width: int,
    ):
        if signed_distance is None or max_radius <= 0:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32)

        hard = (mask == 0) | (mask == 255)
        eligible = (np.abs(signed_distance) <= max_radius) & hard
        if exclusion is not None:
            eligible &= ~exclusion

        local_y, local_x = np.nonzero(eligible)
        if not len(local_x):
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32)

        indices = (
            (local_y.astype(np.int64) + y_offset) * full_width
            + local_x.astype(np.int64)
            + x_offset
        )
        distances = signed_distance[local_y, local_x].astype(
            np.float32, copy=True
        )
        return indices, distances

    def prepare(self, mask: np.ndarray, protected_mask=None) -> None:
        """Build the initial edge-distance cache for an editable mask."""
        source = self._validate_mask(mask)
        height, width = source.shape
        self.reset()
        self._shape = source.shape
        self._max_radius = self._radius_for_shape(source.shape)
        self._protected_exclusion = self._make_protected_exclusion(
            source.shape, protected_mask, self._max_radius
        )
        self._ready = True

        binary = self._make_binary(source, protected_mask)
        if not np.any(binary) or np.all(binary) or self._max_radius <= 0:
            return

        x, y, box_width, box_height = cv2.boundingRect(binary)
        pad = int(ceil(self._max_radius)) + 2
        x0, y0, x1, y1 = self._clip_rect(
            (x - pad, y - pad, box_width + pad * 2, box_height + pad * 2),
            width,
            height,
        )
        binary_roi = binary[y0:y1, x0:x1]
        signed = self._signed_distance(binary_roi)
        exclusion_roi = (
            self._protected_exclusion[y0:y1, x0:x1]
            if self._protected_exclusion is not None
            else None
        )
        self._indices, self._distances = self._samples_for_region(
            source[y0:y1, x0:x1], signed, exclusion_roi,
            self._max_radius, x0, y0, width,
        )

    def update_region(
        self,
        mask: np.ndarray,
        changed_rect: Rect,
        protected_mask=None,
    ) -> None:
        """Refresh only cached edge distances affected by a brush stroke.

        ``changed_rect`` bounds the nonzero brush footprint in source-mask
        coordinates. A halo twice the maximum feather radius ensures the
        distance transform in the updated core does not see artificial crop
        edges.
        """
        source = self._validate_mask(mask)
        if not self._ready or source.shape != self._shape:
            self.prepare(source, protected_mask)
            return

        height, width = source.shape
        pad = int(ceil(self._max_radius)) + 2
        bx, by, bw, bh = self._clip_rect(changed_rect, width, height)
        if bx >= bw or by >= bh:
            return

        update_rect = self._clip_rect(
            (bx - pad, by - pad, (bw - bx) + pad * 2, (bh - by) + pad * 2),
            width,
            height,
        )
        ux0, uy0, ux1, uy1 = update_rect
        context_rect = self._clip_rect(
            (ux0 - pad, uy0 - pad, (ux1 - ux0) + pad * 2,
             (uy1 - uy0) + pad * 2),
            width,
            height,
        )
        cx0, cy0, cx1, cy1 = context_rect

        protected = None
        if protected_mask is not None:
            candidate = np.asarray(protected_mask)
            if candidate.shape == source.shape:
                protected = candidate[cy0:cy1, cx0:cx1]

        binary = self._make_binary(
            source[cy0:cy1, cx0:cx1], protected
        )
        signed_context = self._signed_distance(binary)
        core_y0, core_x0 = uy0 - cy0, ux0 - cx0
        core_y1 = core_y0 + (uy1 - uy0)
        core_x1 = core_x0 + (ux1 - ux0)
        signed_core = (
            signed_context[core_y0:core_y1, core_x0:core_x1]
            if signed_context is not None
            else None
        )
        exclusion_core = (
            self._protected_exclusion[uy0:uy1, ux0:ux1]
            if self._protected_exclusion is not None
            else None
        )
        new_indices, new_distances = self._samples_for_region(
            source[uy0:uy1, ux0:ux1], signed_core, exclusion_core,
            self._max_radius, ux0, uy0, width,
        )

        old_x = self._indices % width
        old_y = self._indices // width
        keep_old = ~(
            (old_x >= ux0) & (old_x < ux1)
            & (old_y >= uy0) & (old_y < uy1)
        )
        self._indices = np.concatenate((self._indices[keep_old], new_indices))
        self._distances = np.concatenate(
            (self._distances[keep_old], new_distances)
        )

    def apply(self, mask: np.ndarray, percentage: int,
              protected_mask=None) -> np.ndarray:
        """Return a fresh mask with a local smoothstep alpha transition."""
        source = self._validate_mask(mask)
        value = max(0, min(100, int(percentage)))
        result = source.copy()
        if value == 0:
            return result

        if not self._ready or source.shape != self._shape:
            self.prepare(source, protected_mask)

        radius = self._max_radius * (value / 100.0)
        if radius <= 0 or not len(self._indices):
            return result

        active = np.abs(self._distances) < radius
        if not np.any(active):
            return result

        distances = self._distances[active]
        position = np.clip((distances + radius) / (2.0 * radius), 0.0, 1.0)
        alpha = position * position * (3.0 - 2.0 * position)
        values = np.rint(alpha * 255.0).astype(np.uint8)
        result.reshape(-1)[self._indices[active]] = values
        return result

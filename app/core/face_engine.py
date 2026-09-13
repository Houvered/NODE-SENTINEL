# -*- coding: utf-8 -*-
"""
Core Face Recognition Engine for NODE SENTINEL.

Provides face detection, feature extraction (512-dim embedding), image validation,
and cosine similarity computation using native PIL and NumPy.
Designed to run cleanly without external heavyweight C-extension dependencies,
using only demo/synthetic biometric data.
"""
from __future__ import annotations

import io
import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image

from app.models.face_models import BoundingBox, DetectedFace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom Exception Hierarchy
# ---------------------------------------------------------------------------

class FaceRecognitionError(Exception):
    """Base exception for all face recognition operations."""
    pass


class InvalidImageError(FaceRecognitionError):
    """Raised when an image file is missing, empty, corrupted, or unreadable."""
    pass


class NoFaceDetectedError(FaceRecognitionError):
    """Raised when no face is found in the provided image."""
    pass


class MultipleFacesError(FaceRecognitionError):
    """Raised when multiple faces are detected in an image expecting a single subject."""
    def __init__(self, message: str, face_count: int = 0, faces: Optional[List[DetectedFace]] = None):
        super().__init__(message)
        self.face_count = face_count
        self.faces = faces or []


class DuplicateRegistrationError(FaceRecognitionError):
    """Raised when attempting to register an identity that already exists."""
    def __init__(self, message: str, person_id: str = ""):
        super().__init__(message)
        self.person_id = person_id


class MissingEmbeddingError(FaceRecognitionError):
    """Raised when a face embedding vector cannot be extracted, is missing, or is empty."""
    pass


# ---------------------------------------------------------------------------
# Image Validation and Loading
# ---------------------------------------------------------------------------

def load_and_validate_image(
    image_input: Union[str, Path, bytes, io.BytesIO, Image.Image]
) -> Image.Image:
    """
    Load an image from filepath, raw bytes, BytesIO stream, or PIL Image,
    validating readability, non-zero file size, and minimum dimensions.

    Raises:
        InvalidImageError: If the image cannot be read, does not exist, or is corrupted.
    """
    if image_input is None:
        raise InvalidImageError("Image input cannot be None.")

    try:
        if isinstance(image_input, Image.Image):
            img = image_input
        elif isinstance(image_input, (str, Path)):
            path = Path(image_input)
            if not path.exists():
                raise InvalidImageError(f"Image file does not exist: '{path}'")
            if not path.is_file():
                raise InvalidImageError(f"Path is not a regular file: '{path}'")
            if path.stat().st_size == 0:
                raise InvalidImageError(f"Image file is empty (0 bytes): '{path}'")
            try:
                img = Image.open(path)
                img.load()  # verify integrity
            except Exception as e:
                raise InvalidImageError(f"Cannot decode image file '{path}': {e}") from e
        elif isinstance(image_input, (bytes, bytearray)):
            if len(image_input) == 0:
                raise InvalidImageError("Image bytes payload is empty (0 bytes).")
            try:
                img = Image.open(io.BytesIO(image_input))
                img.load()
            except Exception as e:
                raise InvalidImageError(f"Cannot decode image from bytes: {e}") from e
        elif isinstance(image_input, io.BytesIO):
            image_input.seek(0)
            data = image_input.read()
            if len(data) == 0:
                raise InvalidImageError("BytesIO buffer is empty.")
            img = Image.open(io.BytesIO(data))
            img.load()
        else:
            raise InvalidImageError(f"Unsupported image input type: {type(image_input).__name__}")

        # Check minimal dimensions
        if img.width < 10 or img.height < 10:
            raise InvalidImageError(
                f"Image dimensions too small ({img.width}x{img.height}). Minimum is 10x10."
            )

        # Convert to standard RGB mode
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")

        return img

    except InvalidImageError:
        raise
    except Exception as exc:
        raise InvalidImageError(f"Failed to process image: {exc}") from exc


# ---------------------------------------------------------------------------
# Face Detection
# ---------------------------------------------------------------------------

def detect_faces(
    image_input: Union[str, Path, bytes, io.BytesIO, Image.Image]
) -> List[DetectedFace]:
    """
    Detect candidate faces in the image.

    Uses skin-tone chrominance analysis, spatial component segmentation,
    and aspect-ratio verification. Designed to work reliably on both synthetic
    avatars and standard portrait photos without requiring external binary engines.

    Returns:
        List[DetectedFace]: Bounding boxes and confidence scores for each face.
    """
    img = load_and_validate_image(image_input)
    w, h = img.size
    arr = np.asarray(img, dtype=np.int32)
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # Generalized human skin-tone color filter (works across diverse skin tones & synthetic portraits)
    skin_mask = (
        (R > 75) & (G > 35) & (B > 18) &
        (R > G) & (R > B) &
        (np.abs(R - G) > 8) &
        ((np.maximum.reduce([R, G, B]) - np.minimum.reduce([R, G, B])) > 12)
    )

    total_skin_pixels = int(skin_mask.sum())
    min_pixels_required = int(w * h * 0.012)

    if total_skin_pixels < min_pixels_required:
        return []

    # 1D column projection to separate horizontally distinct faces
    col_sums = skin_mask.sum(axis=0)
    col_threshold = max(2, int(h * 0.04))
    active_cols = np.where(col_sums > col_threshold)[0]

    if len(active_cols) == 0:
        return []

    # Identify spatial horizontal components separated by gaps
    gap_threshold = max(3, int(w * 0.05))
    gaps = np.where(np.diff(active_cols) > gap_threshold)[0]
    segments: List[Tuple[int, int]] = []
    start = active_cols[0]
    for g in gaps:
        end = active_cols[g]
        segments.append((start, end))
        start = active_cols[g + 1]
    segments.append((start, active_cols[-1]))

    detected: List[DetectedFace] = []
    face_idx = 0

    for seg_x0, seg_x1 in segments:
        box_w = seg_x1 - seg_x0
        # Require minimal face width relative to image
        if box_w < max(8, int(w * 0.07)):
            continue

        seg_skin = skin_mask[:, seg_x0:seg_x1 + 1]
        row_sums = seg_skin.sum(axis=1)
        row_threshold = max(2, int(box_w * 0.08))
        active_rows = np.where(row_sums > row_threshold)[0]
        if len(active_rows) == 0:
            continue

        seg_y0, seg_y1 = int(active_rows[0]), int(active_rows[-1])
        box_h = seg_y1 - seg_y0

        if box_h < max(8, int(h * 0.07)):
            continue

        # Add modest padding around detected face region
        pad_x = int(box_w * 0.10)
        pad_y = int(box_h * 0.12)
        x = max(0, seg_x0 - pad_x)
        y = max(0, seg_y0 - pad_y)
        width = min(w - x, box_w + 2 * pad_x)
        height = min(h - y, box_h + 2 * pad_y)

        # Calculate confidence based on skin density and aspect ratio
        aspect = width / max(1, height)
        aspect_score = 1.0 - min(0.5, abs(aspect - 0.85) * 0.5)
        density = float(seg_skin.sum()) / max(1, (width * height))
        score = min(0.99, max(0.50, round(aspect_score * 0.6 + density * 0.4, 3)))

        bbox = BoundingBox(x=x, y=y, width=width, height=height)
        detected.append(DetectedFace(
            face_index=face_idx,
            bounding_box=bbox,
            detection_score=score,
            embedding=None
        ))
        face_idx += 1

    return detected


# ---------------------------------------------------------------------------
# 512-dim Embedding Extraction
# ---------------------------------------------------------------------------

def extract_embedding(
    image_input: Union[str, Path, bytes, io.BytesIO, Image.Image],
    bounding_box: Optional[BoundingBox] = None
) -> List[float]:
    """
    Extract a normalized 512-dimensional feature embedding vector from a face image.

    Normalizes the face region to canonical 112x112, applies zero-mean per-channel
    normalization, extracts multi-scale spatial gradients and color patch statistics,
    and L2-normalizes the resulting 512-dim vector.

    Raises:
        InvalidImageError: If the image cannot be read.
        MissingEmbeddingError: If the face region has near-zero energy or cannot be extracted.
    """
    img = load_and_validate_image(image_input)

    # Crop to bounding box if provided
    if bounding_box is not None:
        x, y, bw, bh = bounding_box.x, bounding_box.y, bounding_box.width, bounding_box.height
        # Ensure within image boundaries
        x = max(0, min(img.width - 1, x))
        y = max(0, min(img.height - 1, y))
        bw = max(1, min(img.width - x, bw))
        bh = max(1, min(img.height - y, bh))
        face_img = img.crop((x, y, x + bw, y + bh))
    else:
        face_img = img

    # Canonical ArcFace-standard resolution
    face_img = face_img.resize((112, 112), Image.Resampling.BILINEAR)
    arr = np.asarray(face_img, dtype=np.float32)

    # Per-channel zero-centering and standardization
    for c in range(3):
        arr[:, :, c] -= arr[:, :, c].mean()
        std = float(arr[:, :, c].std())
        if std > 1e-4:
            arr[:, :, c] /= std

    # Grayscale conversion for spatial gradients
    gray = arr.mean(axis=2)
    gy, gx = np.gradient(gray)

    # 8x8 spatial grid -> 64 cells, each producing 8 feature descriptors = 512 dims
    grid_h, grid_w = 8, 8
    h_step = 112 // grid_h
    w_step = 112 // grid_w

    feats: List[float] = []
    for i in range(grid_h):
        for j in range(grid_w):
            r0, r1 = i * h_step, (i + 1) * h_step
            c0, c1 = j * w_step, (j + 1) * w_step
            patch_rgb = arr[r0:r1, c0:c1]
            patch_gx = gx[r0:r1, c0:c1]
            patch_gy = gy[r0:r1, c0:c1]

            feats.extend([
                float(patch_rgb[:, :, 0].mean()),
                float(patch_rgb[:, :, 0].std()),
                float(patch_rgb[:, :, 1].mean()),
                float(patch_rgb[:, :, 1].std()),
                float(patch_rgb[:, :, 2].mean()),
                float(patch_rgb[:, :, 2].std()),
                float(patch_gx.mean()),
                float(patch_gy.mean()),
            ])

    vec = np.array(feats, dtype=np.float32)
    norm = float(np.linalg.norm(vec))

    if norm <= 1e-6:
        raise MissingEmbeddingError(
            "Extracted feature vector has near-zero energy (featureless or completely flat image)."
        )

    vec = vec / norm
    return [round(float(val), 6) for val in vec]


# ---------------------------------------------------------------------------
# Cosine Similarity
# ---------------------------------------------------------------------------

def cosine_similarity(
    vec1: Union[List[float], np.ndarray],
    vec2: Union[List[float], np.ndarray]
) -> float:
    """
    Compute cosine similarity between two feature vectors in [-1.0, 1.0].
    Normalized feature vectors return dot product directly.
    """
    v1 = np.asarray(vec1, dtype=np.float32)
    v2 = np.asarray(vec2, dtype=np.float32)
    norm1 = float(np.linalg.norm(v1))
    norm2 = float(np.linalg.norm(v2))
    if norm1 <= 1e-6 or norm2 <= 1e-6:
        return 0.0
    sim = float(np.dot(v1, v2) / (norm1 * norm2))
    return max(-1.0, min(1.0, sim))

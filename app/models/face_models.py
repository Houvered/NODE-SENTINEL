# -*- coding: utf-8 -*-
"""
Pydantic data models for the Face Recognition module.

These schemas are fully independent of the graph models and are used only
by the face recognition service and its API routes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Pixel-space bounding box for a detected face."""
    x: int = Field(..., description="Left edge (pixels)")
    y: int = Field(..., description="Top edge (pixels)")
    width: int = Field(..., description="Box width (pixels)")
    height: int = Field(..., description="Box height (pixels)")


class DetectedFace(BaseModel):
    """A single face detected in an image."""
    face_index: int = Field(..., description="0-based index among all faces detected in the image")
    bounding_box: BoundingBox
    detection_score: float = Field(..., ge=0.0, le=1.0, description="Detector confidence score (0-1)")
    embedding: Optional[List[float]] = Field(
        None,
        description="512-dim ArcFace embedding vector (omitted if embedding failed)"
    )


class FaceDetectionResult(BaseModel):
    """Response for a pure face-detection request (no matching)."""
    status: str = Field("ok")
    face_count: int
    faces: List[DetectedFace]
    image_width: Optional[int] = None
    image_height: Optional[int] = None


from datetime import datetime, timezone


class RegisteredIdentity(BaseModel):
    """A known suspect/subject stored in the demo registry."""
    person_id: Optional[str] = Field(None, description="Unique person identifier, e.g. PERSON_TARIQ_AHMAD")
    identity_id: Optional[str] = Field(None, description="Unique identity identifier, alias for person_id")
    name: str = Field(..., description="Subject name")
    case_id: Optional[str] = Field(None, description="Associated police case ID, e.g. CASE_CRIM_2024_089")
    image_path: str = Field(..., description="Absolute or relative path to reference image on disk")
    embedding: Optional[List[float]] = Field(
        None,
        description="512-dim face embedding vector"
    )
    registration_timestamp: Optional[str] = Field(
        None,
        description="ISO 8601 registration timestamp"
    )
    graph_node_id: Optional[str] = Field(
        None,
        description="Corresponding graph node ID (links face to criminal network graph)"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.person_id is None and self.identity_id is not None:
            self.person_id = self.identity_id
        elif self.identity_id is None and self.person_id is not None:
            self.identity_id = self.person_id
        if self.registration_timestamp is None:
            self.registration_timestamp = datetime.now(timezone.utc).isoformat()
        if self.graph_node_id is None and self.person_id is not None:
            self.graph_node_id = self.person_id



class FaceMatch(BaseModel):
    """A single candidate identity match for a query face."""
    identity_id: str
    name: str
    similarity: float = Field(..., ge=0.0, le=1.0, description="Cosine similarity (0 = no match, 1 = identical)")
    confidence_pct: float = Field(..., ge=0.0, le=100.0, description="similarity x 100 for display")
    is_above_threshold: bool
    graph_node_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FaceSearchResult(BaseModel):
    """
    Response when searching the identity registry for a query image.

    Covers three scenarios:
    - No face detected   => face_count == 0, matches == []
    - Face found, no reliable match => matches empty or all is_above_threshold == False
    - Face found, match(es) found   => matches sorted by descending similarity
    """
    status: str = Field(..., description="'ok', 'no_face', 'no_match', or 'error'")
    message: str
    face_count: int = Field(0, description="Number of faces detected in the query image")
    query_face_index: int = Field(
        0, description="Which detected face was used for the search (0 = largest/first)"
    )
    matches: List[FaceMatch] = Field(default_factory=list)
    registry_size: int = Field(0, description="Number of identities searched against")
    threshold_used: float = Field(..., description="The similarity threshold applied")


class FaceServiceStatus(BaseModel):
    """Health/availability status of the face recognition service."""
    available: bool
    backend: str
    registry_size: int
    registry_identities: List[str] = Field(default_factory=list)
    threshold: float
    message: str

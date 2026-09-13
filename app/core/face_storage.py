# -*- coding: utf-8 -*-
"""
Face Recognition Storage Layer for NODE SENTINEL.

Provides persistent storage, retrieval, indexing, enrollment, and search
for criminal identity face biometric records.
Persists records in an atomic JSON document store under sample_data/face_database/.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
from PIL import Image

from app.config import settings
from app.core.face_engine import (
    FaceRecognitionError,
    InvalidImageError,
    NoFaceDetectedError,
    MultipleFacesError,
    DuplicateRegistrationError,
    MissingEmbeddingError,
    load_and_validate_image,
    detect_faces,
    extract_embedding,
    cosine_similarity,
)
from app.models.face_models import (
    FaceMatch,
    FaceSearchResult,
    RegisteredIdentity,
)

logger = logging.getLogger(__name__)


class FaceStorage:
    """
    Storage manager for registered face identities and biometric feature embeddings.

    Supports:
    - person_id, name, case_id, image_path, face embedding, registration_timestamp
    - register_face() with deduplication and error handling
    - load_embeddings() with vector caching
    - search_face() with cosine similarity matching and threshold verification
    """

    def __init__(
        self,
        db_dir: Optional[Union[str, Path]] = None,
        default_threshold: float = 0.85
    ) -> None:
        if db_dir is None:
            self.db_dir = Path(settings.BASE_DIR) / "sample_data" / "face_database"
        else:
            self.db_dir = Path(db_dir)

        self.images_dir = self.db_dir / "images"
        self.registry_file = self.db_dir / "registry.json"
        self.default_threshold = default_threshold

        # Thread safety lock for concurrent reads/writes
        self._lock = threading.RLock()

        # In-memory index
        self._registry: Dict[str, RegisteredIdentity] = {}
        self._embeddings_cache: Dict[str, np.ndarray] = {}

        # Ensure directory structures exist
        self.images_dir.mkdir(parents=True, exist_ok=True)

        # Load existing registry if available
        self._load_registry()

    # -----------------------------------------------------------------------
    # Internal Persistence Helpers
    # -----------------------------------------------------------------------

    def _load_registry(self) -> None:
        """Load registered identities from registry.json into in-memory store."""
        with self._lock:
            self._registry.clear()
            self._embeddings_cache.clear()

            if not self.registry_file.exists():
                logger.info(f"No existing face registry found at {self.registry_file}. Starting fresh.")
                return

            try:
                with open(self.registry_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                identities = data.get("identities", [])
                for item in identities:
                    try:
                        record = RegisteredIdentity(**item)
                        p_id = record.person_id or record.identity_id
                        if p_id:
                            self._registry[p_id] = record
                            if record.embedding and len(record.embedding) > 0:
                                vec = np.asarray(record.embedding, dtype=np.float32)
                                norm = float(np.linalg.norm(vec))
                                if norm > 1e-6:
                                    self._embeddings_cache[p_id] = vec / norm
                    except Exception as parse_err:
                        logger.warning(f"Skipping malformed identity entry in {self.registry_file}: {parse_err}")

                logger.info(f"Loaded {len(self._registry)} identities ({len(self._embeddings_cache)} embeddings) from {self.registry_file}.")

            except Exception as exc:
                logger.error(f"Failed to read face registry file '{self.registry_file}': {exc}")

    def _save_registry(self) -> None:
        """Atomically persist in-memory registry to disk as formatted JSON."""
        with self._lock:
            self.db_dir.mkdir(parents=True, exist_ok=True)
            temp_file = self.registry_file.with_suffix(".tmp")

            records_list = [record.model_dump() for record in self._registry.values()]
            payload = {
                "version": "1.0.0",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "identity_count": len(records_list),
                "identities": records_list
            }

            try:
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                # Atomic replace
                shutil.move(str(temp_file), str(self.registry_file))
            except Exception as e:
                if temp_file.exists():
                    temp_file.unlink(missing_ok=True)
                raise IOError(f"Failed to write face database file '{self.registry_file}': {e}") from e

    # -----------------------------------------------------------------------
    # Core Public Interface: register_face
    # -----------------------------------------------------------------------

    def register_face(
        self,
        image_path: Union[str, Path],
        person_id: str,
        name: str,
        case_id: Optional[str] = None,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        overwrite: bool = False,
    ) -> RegisteredIdentity:
        """
        Register a new identity in the face database.

        Args:
            image_path: Path to the reference portrait image file.
            person_id: Unique identifier for the person (e.g. 'PERSON_TARIQ_AHMAD').
            name: Full name of the individual.
            case_id: Optional police/investigation case ID (e.g. 'CASE_CRIM_2024_089').
            embedding: Optional pre-extracted 512-dim embedding. If omitted, will be extracted from image.
            metadata: Optional arbitrary investigative metadata dictionary.
            overwrite: If True, allows updating an existing registered identity.

        Raises:
            ValueError: If person_id or name is empty.
            DuplicateRegistrationError: If person_id is already registered and overwrite is False.
            InvalidImageError: If the image is invalid, missing, or corrupted.
            NoFaceDetectedError: If no face is found in the image.
            MultipleFacesError: If more than 1 face is found in the portrait.
            MissingEmbeddingError: If an embedding cannot be obtained or is empty.

        Returns:
            RegisteredIdentity: The fully enrolled biometric identity record.
        """
        if not person_id or not str(person_id).strip():
            raise ValueError("person_id cannot be empty.")
        if not name or not str(name).strip():
            raise ValueError("name cannot be empty.")

        clean_person_id = str(person_id).strip()
        clean_name = str(name).strip()

        with self._lock:
            # 1. Check duplicate registration
            if clean_person_id in self._registry and not overwrite:
                raise DuplicateRegistrationError(
                    f"Identity '{clean_person_id}' is already registered in the face database.",
                    person_id=clean_person_id
                )

            # 2. Validate and load image
            img = load_and_validate_image(image_path)

            # 3. Determine or extract embedding
            if embedding is not None:
                if not isinstance(embedding, list) or len(embedding) == 0:
                    raise MissingEmbeddingError("Provided embedding vector is empty or invalid.")
                final_embedding = [float(v) for v in embedding]
            else:
                # Detect faces in reference image
                detected = detect_faces(img)
                if len(detected) == 0:
                    raise NoFaceDetectedError(
                        f"No face detected in reference image '{image_path}'. Face enrollment requires a visible face."
                    )
                if len(detected) > 1:
                    raise MultipleFacesError(
                        f"Multiple faces ({len(detected)}) detected in reference image '{image_path}'. Reference portrait must contain exactly one face.",
                        face_count=len(detected),
                        faces=detected
                    )

                # Extract embedding from detected bounding box
                face = detected[0]
                final_embedding = extract_embedding(img, face.bounding_box)
                if not final_embedding or len(final_embedding) == 0:
                    raise MissingEmbeddingError("Face embedding extraction yielded empty vector.")

            # 4. Construct RegisteredIdentity record
            timestamp = datetime.now(timezone.utc).isoformat()
            resolved_image_path = str(Path(image_path).resolve())

            record = RegisteredIdentity(
                person_id=clean_person_id,
                identity_id=clean_person_id,
                name=clean_name,
                case_id=case_id,
                image_path=resolved_image_path,
                embedding=final_embedding,
                registration_timestamp=timestamp,
                graph_node_id=clean_person_id,
                metadata=metadata or {}
            )

            # 5. Store in memory and update embeddings cache
            self._registry[clean_person_id] = record
            vec = np.asarray(final_embedding, dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            if norm > 1e-6:
                self._embeddings_cache[clean_person_id] = vec / norm

            # 6. Persist to disk
            self._save_registry()

            logger.info(f"Registered face identity: '{clean_name}' ({clean_person_id}), case={case_id}")
            return record

    # -----------------------------------------------------------------------
    # Core Public Interface: load_embeddings
    # -----------------------------------------------------------------------

    def load_embeddings(self) -> Dict[str, np.ndarray]:
        """
        Load and return a dictionary of normalized face embeddings for all registered identities.

        Returns:
            Dict[str, np.ndarray]: Mapping of person_id -> unit-normalized 512-dim embedding array.
        """
        with self._lock:
            # Sync cache with in-memory registry
            for p_id, record in self._registry.items():
                if p_id not in self._embeddings_cache:
                    if record.embedding and len(record.embedding) > 0:
                        vec = np.asarray(record.embedding, dtype=np.float32)
                        norm = float(np.linalg.norm(vec))
                        if norm > 1e-6:
                            self._embeddings_cache[p_id] = vec / norm
                    else:
                        logger.warning(f"Identity '{p_id}' has no embedding vector in registry.")

            return dict(self._embeddings_cache)

    # -----------------------------------------------------------------------
    # Core Public Interface: search_face
    # -----------------------------------------------------------------------

    def search_face(
        self,
        query_image: Optional[Union[str, Path, bytes, io.BytesIO, Image.Image]] = None,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 5,
        threshold: Optional[float] = None,
        raise_on_error: bool = True,
    ) -> FaceSearchResult:
        """
        Search the registered face database using a query image or pre-computed embedding.

        Args:
            query_image: Filepath, bytes, or PIL Image containing the query face.
            query_embedding: Optional 512-dim embedding vector directly provided.
            top_k: Maximum number of top matching identities to return.
            threshold: Similarity cutoff in [0, 1]. Defaults to self.default_threshold (0.85).
            raise_on_error: If True, raises exceptions on invalid image / no face / multiple faces.
                            If False, returns a FaceSearchResult with error status.

        Raises:
            InvalidImageError: If query_image is invalid or corrupt.
            NoFaceDetectedError: If no face is detected in query_image and raise_on_error is True.
            MultipleFacesError: If multiple faces are found and raise_on_error is True.
            MissingEmbeddingError: If no embedding can be generated or both inputs are missing.

        Returns:
            FaceSearchResult: Ranked candidate matches and similarity metrics.
        """
        applied_threshold = threshold if threshold is not None else self.default_threshold

        # 1. Resolve query vector and face count
        q_vec: Optional[np.ndarray] = None
        face_count = 0
        query_face_idx = 0

        try:
            if query_embedding is not None:
                if not isinstance(query_embedding, (list, np.ndarray)) or len(query_embedding) == 0:
                    raise MissingEmbeddingError("Query embedding vector is empty or invalid.")
                q_arr = np.asarray(query_embedding, dtype=np.float32)
                norm = float(np.linalg.norm(q_arr))
                if norm <= 1e-6:
                    raise MissingEmbeddingError("Query embedding vector has zero magnitude.")
                q_vec = q_arr / norm
                face_count = 1
                query_face_idx = 0

            elif query_image is not None:
                img = load_and_validate_image(query_image)
                detected = detect_faces(img)
                face_count = len(detected)

                if face_count == 0:
                    if raise_on_error:
                        raise NoFaceDetectedError("No face detected in query image.")
                    return FaceSearchResult(
                        status="no_face",
                        message="No face detected in query image.",
                        face_count=0,
                        query_face_index=0,
                        matches=[],
                        registry_size=len(self._registry),
                        threshold_used=applied_threshold,
                    )

                if face_count > 1 and raise_on_error:
                    raise MultipleFacesError(
                        f"Multiple faces ({face_count}) detected in query image.",
                        face_count=face_count,
                        faces=detected,
                    )

                # Select primary face (first detected or largest)
                primary_face = detected[0]
                query_face_idx = primary_face.face_index
                raw_emb = extract_embedding(img, primary_face.bounding_box)
                q_arr = np.asarray(raw_emb, dtype=np.float32)
                norm = float(np.linalg.norm(q_arr))
                if norm <= 1e-6:
                    raise MissingEmbeddingError("Extracted query embedding has near-zero energy.")
                q_vec = q_arr / norm

            else:
                raise MissingEmbeddingError("Neither query_image nor query_embedding was provided.")

        except (InvalidImageError, NoFaceDetectedError, MultipleFacesError, MissingEmbeddingError):
            if raise_on_error:
                raise
            return FaceSearchResult(
                status="error",
                message="Error processing query face.",
                face_count=face_count,
                query_face_index=query_face_idx,
                matches=[],
                registry_size=len(self._registry),
                threshold_used=applied_threshold,
            )

        # 2. Match against registered embeddings
        known_embeddings = self.load_embeddings()
        registry_size = len(self._registry)

        if not known_embeddings:
            return FaceSearchResult(
                status="no_match",
                message="Face database is empty; no enrolled identities to match against.",
                face_count=face_count,
                query_face_index=query_face_idx,
                matches=[],
                registry_size=0,
                threshold_used=applied_threshold,
            )

        matches: List[FaceMatch] = []
        with self._lock:
            for p_id, db_vec in known_embeddings.items():
                record = self._registry.get(p_id)
                if not record:
                    continue

                sim = float(np.dot(q_vec, db_vec))
                # Clamp to [0.0, 1.0]
                clamped_sim = max(0.0, min(1.0, sim))
                conf_pct = round(clamped_sim * 100.0, 2)
                is_hit = bool(clamped_sim >= applied_threshold)

                match_meta = dict(record.metadata)
                if record.case_id:
                    match_meta["case_id"] = record.case_id
                if record.image_path:
                    match_meta["image_path"] = record.image_path

                matches.append(FaceMatch(
                    identity_id=p_id,
                    name=record.name,
                    similarity=round(clamped_sim, 4),
                    confidence_pct=conf_pct,
                    is_above_threshold=is_hit,
                    graph_node_id=record.graph_node_id or p_id,
                    metadata=match_meta,
                ))

        # Sort matches by similarity descending
        matches.sort(key=lambda m: m.similarity, reverse=True)
        top_matches = matches[:top_k]

        has_verified_hit = any(m.is_above_threshold for m in top_matches)
        status = "ok" if has_verified_hit else "no_match"
        message = (
            f"Found {len(top_matches)} candidate(s); best match: '{top_matches[0].name}' ({top_matches[0].confidence_pct}%)"
            if top_matches else "No candidates found."
        )

        return FaceSearchResult(
            status=status,
            message=message,
            face_count=face_count,
            query_face_index=query_face_idx,
            matches=top_matches,
            registry_size=registry_size,
            threshold_used=applied_threshold,
        )

    # -----------------------------------------------------------------------
    # Retrieval & Inspection Methods
    # -----------------------------------------------------------------------

    def get_identity(self, person_id: str) -> Optional[RegisteredIdentity]:
        """Retrieve a registered identity by its person_id."""
        with self._lock:
            return self._registry.get(person_id)

    def list_identities(self) -> List[RegisteredIdentity]:
        """Return all registered identities in the database."""
        with self._lock:
            return list(self._registry.values())

    def delete_identity(self, person_id: str) -> bool:
        """Delete an identity and its cached embedding from the database."""
        with self._lock:
            if person_id in self._registry:
                del self._registry[person_id]
                self._embeddings_cache.pop(person_id, None)
                self._save_registry()
                logger.info(f"Deleted identity '{person_id}' from face database.")
                return True
            return False

    def clear(self) -> None:
        """Clear all registered identities."""
        with self._lock:
            self._registry.clear()
            self._embeddings_cache.clear()
            self._save_registry()
            logger.info("Cleared all face database records.")


# ---------------------------------------------------------------------------
# Singleton Factory
# ---------------------------------------------------------------------------

_face_storage_instance: Optional[FaceStorage] = None
_instance_lock = threading.Lock()


def get_face_storage() -> FaceStorage:
    """Retrieve or initialize the global FaceStorage singleton."""
    global _face_storage_instance
    if _face_storage_instance is None:
        with _instance_lock:
            if _face_storage_instance is None:
                _face_storage_instance = FaceStorage()
    return _face_storage_instance

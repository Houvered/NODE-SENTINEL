# -*- coding: utf-8 -*-
"""
Test Suite for Face Registration and Search Storage Layer.

Tests:
1. Valid face registration with all metadata fields (person_id, name, case_id, image_path, embedding, timestamp).
2. Error handling: InvalidImageError (missing/corrupted image).
3. Error handling: NoFaceDetectedError (image with zero faces).
4. Error handling: MultipleFacesError (image with multiple faces).
5. Error handling: DuplicateRegistrationError (duplicate person_id).
6. Error handling: MissingEmbeddingError (empty/missing embedding vector).
7. load_embeddings(): verifies vector shapes, unit norms, and dictionary keys.
8. search_face(): probe matching known suspect (Tariq Ahmad) with high confidence.
9. search_face(): probe with unknown individual verifying threshold rejection.
10. Persistence: ensures fresh FaceStorage reloads identical records from disk.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure root directory is on Python path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np
from PIL import Image

from app.core.demo_face_data import generate_synthetic_avatar, seed_demo_face_database
from app.core.face_engine import (
    DuplicateRegistrationError,
    InvalidImageError,
    MissingEmbeddingError,
    MultipleFacesError,
    NoFaceDetectedError,
)
from app.core.face_storage import FaceStorage


def run_tests() -> bool:
    print("=" * 70)
    print("RUNNING FACE REGISTRATION & SEARCH FOUNDATION TESTS")
    print("=" * 70)

    # Use an isolated temporary directory for initial tests
    temp_dir = Path(tempfile.mkdtemp(prefix="sentinel_face_test_"))
    storage = FaceStorage(db_dir=temp_dir, default_threshold=0.85)

    images_dir = temp_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Generate test fixtures
    valid_face_path = images_dir / "valid_suspect.png"
    generate_synthetic_avatar(skin=(215, 175, 140), hair=(30, 30, 35)).save(valid_face_path)

    probe_face_path = images_dir / "probe_similar.png"
    generate_synthetic_avatar(skin=(217, 173, 138), hair=(30, 30, 35)).save(probe_face_path)

    unknown_face_path = images_dir / "unknown_person.png"
    generate_synthetic_avatar(skin=(160, 110, 80), hair=(150, 90, 40)).save(unknown_face_path)

    no_face_path = images_dir / "landscape_no_face.png"
    img_noface = Image.new("RGB", (200, 200), (30, 144, 255))
    img_noface.save(no_face_path)

    corrupt_path = images_dir / "corrupted.png"
    with open(corrupt_path, "wb") as f:
        f.write(b"NOT_A_VALID_IMAGE_BYTES_PAYLOAD")

    multi_face_path = images_dir / "two_faces.png"
    img_multi = Image.new("RGB", (420, 200), (245, 245, 250))
    a1 = generate_synthetic_avatar(skin=(215, 175, 140), hair=(25, 25, 30), width=200, height=200)
    a2 = generate_synthetic_avatar(skin=(190, 145, 110), hair=(50, 35, 25), width=200, height=200)
    img_multi.paste(a1, (10, 0))
    img_multi.paste(a2, (210, 0))
    img_multi.save(multi_face_path)

    passed_tests = 0
    total_tests = 10

    # -----------------------------------------------------------------------
    # TEST 1: Valid Face Registration with required fields
    # -----------------------------------------------------------------------
    print("\n[TEST 1] Registering valid face identity...")
    record = storage.register_face(
        image_path=valid_face_path,
        person_id="PERSON_TEST_001",
        name="Test Suspect One",
        case_id="CASE_TEST_2026_01",
        metadata={"notes": "Primary test subject"},
    )
    assert record.person_id == "PERSON_TEST_001", f"Expected PERSON_TEST_001, got {record.person_id}"
    assert record.name == "Test Suspect One", f"Expected 'Test Suspect One', got {record.name}"
    assert record.case_id == "CASE_TEST_2026_01", f"Expected 'CASE_TEST_2026_01', got {record.case_id}"
    assert record.image_path == str(valid_face_path.resolve())
    assert record.embedding is not None and len(record.embedding) == 512, f"Embedding len: {len(record.embedding) if record.embedding else None}"
    assert record.registration_timestamp is not None and len(record.registration_timestamp) > 10
    print(f"  -> SUCCESS: Registered '{record.name}' with 512-dim embedding at {record.registration_timestamp[:19]}")
    passed_tests += 1

    # -----------------------------------------------------------------------
    # TEST 2: Error Handling - Invalid Image
    # -----------------------------------------------------------------------
    print("\n[TEST 2] Error handling: Invalid / corrupted image...")
    caught_invalid_file = False
    try:
        storage.register_face(
            image_path=temp_dir / "non_existent_file.png",
            person_id="PERSON_TEST_INV_1",
            name="Invalid File Subject",
        )
    except InvalidImageError as e:
        caught_invalid_file = True
        print(f"  -> Successfully caught expected InvalidImageError on missing file: {e}")

    caught_corrupt_file = False
    try:
        storage.register_face(
            image_path=corrupt_path,
            person_id="PERSON_TEST_INV_2",
            name="Corrupt File Subject",
        )
    except InvalidImageError as e:
        caught_corrupt_file = True
        print(f"  -> Successfully caught expected InvalidImageError on corrupted file: {e}")

    assert caught_invalid_file and caught_corrupt_file, "Failed to catch InvalidImageError"
    passed_tests += 1

    # -----------------------------------------------------------------------
    # TEST 3: Error Handling - No Face Detected
    # -----------------------------------------------------------------------
    print("\n[TEST 3] Error handling: Image with no face...")
    caught_no_face = False
    try:
        storage.register_face(
            image_path=no_face_path,
            person_id="PERSON_TEST_NO_FACE",
            name="Landscape Subject",
        )
    except NoFaceDetectedError as e:
        caught_no_face = True
        print(f"  -> Successfully caught expected NoFaceDetectedError: {e}")

    assert caught_no_face, "Failed to catch NoFaceDetectedError"
    passed_tests += 1

    # -----------------------------------------------------------------------
    # TEST 4: Error Handling - Multiple Faces Detected
    # -----------------------------------------------------------------------
    print("\n[TEST 4] Error handling: Image with multiple faces...")
    caught_multi_face = False
    try:
        storage.register_face(
            image_path=multi_face_path,
            person_id="PERSON_TEST_MULTI",
            name="Group Portrait",
        )
    except MultipleFacesError as e:
        caught_multi_face = True
        print(f"  -> Successfully caught expected MultipleFacesError: face_count={e.face_count}")
        assert e.face_count >= 2, f"Expected face_count >= 2, got {e.face_count}"

    assert caught_multi_face, "Failed to catch MultipleFacesError"
    passed_tests += 1

    # -----------------------------------------------------------------------
    # TEST 5: Error Handling - Duplicate Registration
    # -----------------------------------------------------------------------
    print("\n[TEST 5] Error handling: Duplicate registration...")
    caught_duplicate = False
    try:
        storage.register_face(
            image_path=valid_face_path,
            person_id="PERSON_TEST_001",
            name="Duplicate Subject Name",
            overwrite=False,
        )
    except DuplicateRegistrationError as e:
        caught_duplicate = True
        print(f"  -> Successfully caught expected DuplicateRegistrationError: {e}")
        assert e.person_id == "PERSON_TEST_001"

    assert caught_duplicate, "Failed to catch DuplicateRegistrationError"

    # Verify overwrite=True works
    updated_rec = storage.register_face(
        image_path=valid_face_path,
        person_id="PERSON_TEST_001",
        name="Updated Name",
        overwrite=True,
    )
    assert updated_rec.name == "Updated Name", "Failed to update with overwrite=True"
    print("  -> Successfully verified overwrite=True update functionality")
    passed_tests += 1

    # -----------------------------------------------------------------------
    # TEST 6: Error Handling - Missing Embedding
    # -----------------------------------------------------------------------
    print("\n[TEST 6] Error handling: Missing embedding...")
    caught_missing_emb = False
    try:
        storage.register_face(
            image_path=valid_face_path,
            person_id="PERSON_EMPTY_EMB",
            name="Empty Embedding Subject",
            embedding=[],
        )
    except MissingEmbeddingError as e:
        caught_missing_emb = True
        print(f"  -> Successfully caught expected MissingEmbeddingError: {e}")

    caught_search_missing = False
    try:
        storage.search_face(query_image=None, query_embedding=None)
    except MissingEmbeddingError as e:
        caught_search_missing = True
        print(f"  -> Successfully caught expected MissingEmbeddingError in search: {e}")

    assert caught_missing_emb and caught_search_missing, "Failed to catch MissingEmbeddingError"
    passed_tests += 1

    # -----------------------------------------------------------------------
    # TEST 7: load_embeddings() inspection
    # -----------------------------------------------------------------------
    print("\n[TEST 7] Testing load_embeddings()...")
    embeddings_dict = storage.load_embeddings()
    assert "PERSON_TEST_001" in embeddings_dict, "Registered identity missing from embeddings_dict"
    emb = embeddings_dict["PERSON_TEST_001"]
    assert isinstance(emb, np.ndarray), f"Expected np.ndarray, got {type(emb)}"
    assert emb.shape == (512,), f"Expected shape (512,), got {emb.shape}"
    norm = float(np.linalg.norm(emb))
    assert abs(norm - 1.0) < 1e-4, f"Expected unit norm, got {norm}"
    print(f"  -> SUCCESS: Loaded {len(embeddings_dict)} embedding(s); shape={emb.shape}, norm={norm:.4f}")
    passed_tests += 1

    # -----------------------------------------------------------------------
    # TEST 8: search_face() with matching probe
    # -----------------------------------------------------------------------
    print("\n[TEST 8] Testing search_face() with matching query probe...")
    search_res = storage.search_face(
        query_image=probe_face_path,
        top_k=3,
        threshold=0.85,
    )
    assert search_res.status == "ok", f"Expected status 'ok', got '{search_res.status}'"
    assert len(search_res.matches) > 0, "No matches returned"
    best_match = search_res.matches[0]
    assert best_match.identity_id == "PERSON_TEST_001", f"Expected PERSON_TEST_001, got {best_match.identity_id}"
    assert best_match.is_above_threshold is True, "Match should be above threshold"
    assert best_match.similarity > 0.90, f"Expected similarity > 0.90, got {best_match.similarity}"
    print(f"  -> SUCCESS: Query probe matched '{best_match.name}' ({best_match.identity_id}) with {best_match.confidence_pct}% confidence (sim={best_match.similarity})")
    passed_tests += 1

    # -----------------------------------------------------------------------
    # TEST 9: search_face() with unknown probe (rejection test)
    # -----------------------------------------------------------------------
    print("\n[TEST 9] Testing search_face() with unknown subject (rejection test)...")
    unknown_res = storage.search_face(
        query_image=unknown_face_path,
        top_k=3,
        threshold=0.85,
    )
    if unknown_res.matches:
        top_unk = unknown_res.matches[0]
        assert not top_unk.is_above_threshold, f"Unknown subject falsely exceeded threshold (sim={top_unk.similarity})"
        print(f"  -> SUCCESS: Unknown subject properly rejected (status='{unknown_res.status}', top sim={top_unk.similarity:.4f} < threshold 0.85)")
    else:
        assert unknown_res.status == "no_match"
        print(f"  -> SUCCESS: Unknown subject returned status='{unknown_res.status}' with 0 matches")
    passed_tests += 1

    # -----------------------------------------------------------------------
    # TEST 10: Database Persistence & Production Demo Database Verification
    # -----------------------------------------------------------------------
    print("\n[TEST 10] Testing database persistence and production demo database...")
    # Verify reloading from disk in a fresh storage instance
    reloaded_storage = FaceStorage(db_dir=temp_dir)
    reloaded_identity = reloaded_storage.get_identity("PERSON_TEST_001")
    assert reloaded_identity is not None, "Failed to reload identity from disk"
    assert reloaded_identity.name == "Updated Name", f"Expected 'Updated Name', got {reloaded_identity.name}"
    reloaded_embs = reloaded_storage.load_embeddings()
    assert "PERSON_TEST_001" in reloaded_embs, "Reloaded embeddings missing test identity"

    # Also verify the real demo database in sample_data/face_database/
    prod_storage = FaceStorage(db_dir=ROOT_DIR / "sample_data" / "face_database")
    # Ensure seeded
    seed_demo_face_database(prod_storage, force=False)
    prod_identities = prod_storage.list_identities()
    assert len(prod_identities) >= 4, f"Expected >= 4 demo identities in sample_data/face_database, found {len(prod_identities)}"

    # Test searching production database for Tariq Ahmad probe
    prod_probe = ROOT_DIR / "sample_data" / "face_database" / "images" / "probe_tariq.png"
    if prod_probe.exists():
        prod_search = prod_storage.search_face(query_image=prod_probe, threshold=0.85)
        assert prod_search.status == "ok", f"Expected ok status, got {prod_search.status}"
        assert prod_search.matches[0].identity_id == "PERSON_TARIQ_AHMAD"
        print(f"  -> Verified production demo database: {len(prod_identities)} identities; matched {prod_search.matches[0].name} ({prod_search.matches[0].confidence_pct}%)")

    passed_tests += 1
    print("\n" + "=" * 70)
    print(f"ALL TESTS COMPLETED: {passed_tests} / {total_tests} PASSED.")
    print("=" * 70)
    return True


def test_face_foundation():
    success = run_tests()
    assert success


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

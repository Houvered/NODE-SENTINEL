# -*- coding: utf-8 -*-
"""
Demo Face Database Seeder for NODE SENTINEL.

Generates purely synthetic demo avatar portraits (non-real biometric data)
and populates the face database under sample_data/face_database/.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any

from PIL import Image, ImageDraw

from app.config import settings
from app.core.face_storage import FaceStorage, get_face_storage

logger = logging.getLogger(__name__)


def generate_synthetic_avatar(
    skin: tuple[int, int, int],
    hair: tuple[int, int, int],
    eye_color: tuple[int, int, int] = (30, 30, 40),
    mouth_y: int = 135,
    width: int = 200,
    height: int = 200,
    hair_style: str = "short",
) -> Image.Image:
    """Draw a clean synthetic avatar portrait with facial geometry for testing."""
    img = Image.new("RGB", (width, height), (245, 245, 250))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width - 1, height - 1], outline=(200, 200, 210), width=1)

    # Hair back
    if hair_style == "long":
        d.rectangle([30, 30, 170, 180], fill=hair)
    else:
        d.rectangle([35, 20, 165, 80], fill=hair)

    # Head / face oval
    d.ellipse([40, 35, 160, 175], fill=skin, outline=(100, 70, 50), width=2)

    # Eyes & pupils
    d.ellipse([65, 80, 85, 95], fill=eye_color)
    d.ellipse([115, 80, 135, 95], fill=eye_color)
    d.ellipse([72, 85, 78, 90], fill=(255, 255, 255))
    d.ellipse([122, 85, 128, 90], fill=(255, 255, 255))

    # Eyebrows
    d.line([60, 72, 90, 72], fill=hair, width=3)
    d.line([110, 72, 140, 72], fill=hair, width=3)

    # Nose
    d.line([100, 95, 95, 115], fill=(130, 90, 60), width=2)
    d.line([95, 115, 105, 115], fill=(130, 90, 60), width=2)

    # Mouth
    d.rectangle([80, mouth_y, 120, mouth_y + 6], fill=(160, 60, 60))
    return img


def seed_demo_face_database(storage: FaceStorage, force: bool = False) -> Dict[str, Any]:
    """
    Ensure synthetic demo suspect portraits exist on disk and are registered in FaceStorage.

    Does NOT use real biometric data. Uses synthetic demo avatars matching the
    criminal syndicate network demo dataset.
    """
    images_dir = storage.images_dir
    images_dir.mkdir(parents=True, exist_ok=True)

    # Demo suspect definitions matching syndicate_network.json
    demo_identities = [
        {
            "person_id": "PERSON_TARIQ_AHMAD",
            "name": "Tariq Ahmad",
            "case_id": "CASE_CRIM_2024_089",
            "filename": "suspect_tariq.png",
            "avatar_args": {"skin": (215, 175, 140), "hair": (25, 25, 30), "mouth_y": 135},
            "metadata": {"role": "Syndicate Kingpin", "alias": "The Boss", "risk_tag": "CRITICAL"},
        },
        {
            "person_id": "PERSON_KABIR_MIRZA",
            "name": "Kabir Mirza",
            "case_id": "CASE_CRIM_2024_089",
            "filename": "suspect_kabir.png",
            "avatar_args": {"skin": (190, 145, 110), "hair": (50, 35, 25), "mouth_y": 138},
            "metadata": {"role": "Operations Commander & Field Lieutenant", "risk_tag": "CRITICAL"},
        },
        {
            "person_id": "PERSON_POOJA_RATHI",
            "name": "Pooja Rathi",
            "case_id": "CASE_CRIM_2024_089",
            "filename": "suspect_pooja.png",
            "avatar_args": {"skin": (225, 185, 150), "hair": (30, 20, 20), "mouth_y": 133, "hair_style": "long"},
            "metadata": {"role": "Hawala Conduit & Money Mule Operator", "risk_tag": "HIGH"},
        },
        {
            "person_id": "PERSON_DEEPAK_AGARWAL",
            "name": "Deepak Agarwal",
            "case_id": "CASE_CRIM_2024_089",
            "filename": "suspect_deepak.png",
            "avatar_args": {"skin": (205, 160, 125), "hair": (60, 60, 65), "mouth_y": 140},
            "metadata": {"role": "Logistics & Safehouse Facilitator", "risk_tag": "MEDIUM"},
        },
    ]

    registered_count = 0
    for item in demo_identities:
        img_path = images_dir / item["filename"]
        if not img_path.exists() or force:
            avatar = generate_synthetic_avatar(**item["avatar_args"])
            avatar.save(img_path)

        # Check if already registered
        existing = storage.get_identity(item["person_id"])
        if existing is None or force:
            storage.register_face(
                image_path=img_path,
                person_id=item["person_id"],
                name=item["name"],
                case_id=item["case_id"],
                metadata=item["metadata"],
                overwrite=force,
            )
            registered_count += 1

    # Also ensure test edge-case assets exist
    multi_path = images_dir / "test_multi_face.png"
    if not multi_path.exists() or force:
        img_multi = Image.new("RGB", (420, 200), (245, 245, 250))
        d_m = ImageDraw.Draw(img_multi)
        d_m.rectangle([25, 20, 155, 80], fill=(25, 25, 30))
        d_m.ellipse([30, 35, 150, 175], fill=(215, 175, 140), outline=(100, 70, 50), width=2)
        d_m.ellipse([55, 80, 75, 95], fill=(30, 30, 40))
        d_m.ellipse([105, 80, 125, 95], fill=(30, 30, 40))
        d_m.rectangle([70, 135, 110, 141], fill=(160, 60, 60))

        d_m.rectangle([265, 20, 395, 80], fill=(50, 35, 25))
        d_m.ellipse([270, 35, 390, 175], fill=(190, 145, 110), outline=(100, 70, 50), width=2)
        d_m.ellipse([295, 80, 315, 95], fill=(30, 30, 40))
        d_m.ellipse([345, 80, 365, 95], fill=(30, 30, 40))
        d_m.rectangle([310, 135, 350, 141], fill=(160, 60, 60))
        img_multi.save(multi_path)

    no_face_path = images_dir / "test_no_face.png"
    if not no_face_path.exists() or force:
        img_no_face = Image.new("RGB", (200, 200), (70, 130, 220))
        d_nf = ImageDraw.Draw(img_no_face)
        d_nf.rectangle([0, 120, 200, 200], fill=(34, 139, 34))
        img_no_face.save(no_face_path)

    corrupt_path = images_dir / "test_corrupt.png"
    if not corrupt_path.exists() or force:
        with open(corrupt_path, "wb") as f:
            f.write(b"CORRUPTED_NON_IMAGE_DATA_HEADER_ERROR")

    probe_tariq_path = images_dir / "probe_tariq.png"
    if not probe_tariq_path.exists() or force:
        avatar_tariq_probe = generate_synthetic_avatar(
            skin=(217, 173, 138), hair=(25, 25, 30), mouth_y=136
        )
        avatar_tariq_probe.save(probe_tariq_path)

    probe_unknown_path = images_dir / "probe_unknown.png"
    if not probe_unknown_path.exists() or force:
        avatar_unknown = generate_synthetic_avatar(
            skin=(165, 115, 85), hair=(150, 100, 40), mouth_y=142
        )
        avatar_unknown.save(probe_unknown_path)

    return {
        "status": "seeded",
        "newly_registered": registered_count,
        "total_identities": len(storage.list_identities()),
        "registry_file": str(storage.registry_file),
    }


if __name__ == "__main__":
    storage = get_face_storage()
    res = seed_demo_face_database(storage, force=True)
    print("Seed result:", res)

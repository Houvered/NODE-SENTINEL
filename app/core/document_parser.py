import io
import os
import asyncio
import logging
from pathlib import Path
from typing import Optional
from PIL import Image

logger = logging.getLogger(__name__)

# Try importing fitz (PyMuPDF)
try:
    import fitz
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("PyMuPDF (fitz) is not installed. PDF extraction will be limited.")

# Try importing winocr
try:
    import winocr
    WINOCR_AVAILABLE = True
except ImportError:
    WINOCR_AVAILABLE = False

# Try importing pytesseract as optional fallback
try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False


class DocumentParser:
    """Extracts text content from uploaded PDF reports and photo/image copies of FIRs."""

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
    PDF_EXTENSIONS = {".pdf"}

    def __init__(self):
        self.winocr_available = WINOCR_AVAILABLE
        self.pymupdf_available = PYMUPDF_AVAILABLE
        self.pytesseract_available = PYTESSERACT_AVAILABLE

    async def extract_text_from_file(self, file_bytes: bytes, filename: str) -> str:
        """Main async entry point to extract text from an uploaded document or image file."""
        ext = Path(filename).suffix.lower()

        if ext in self.PDF_EXTENSIONS:
            return await self.extract_text_from_pdf(file_bytes)
        elif ext in self.IMAGE_EXTENSIONS:
            return await self.extract_text_from_image(file_bytes)
        else:
            supported = ", ".join(sorted(self.PDF_EXTENSIONS | self.IMAGE_EXTENSIONS))
            raise ValueError(f"Unsupported file format '{ext}'. Supported formats are: {supported}")

    async def extract_text_from_pdf(self, file_bytes: bytes) -> str:
        """Extract text from PDF, automatically falling back to OCR for scanned pages."""
        if not self.pymupdf_available:
            raise RuntimeError("PyMuPDF (fitz) is not available for PDF extraction.")

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        total_pages = len(doc)
        if total_pages == 0:
            return ""

        extracted_pages = []
        scanned_pages = []

        # 1. First pass: extract text from digital PDF pages
        for page_num in range(total_pages):
            page = doc[page_num]
            text = page.get_text("text").strip()
            if len(text) > 25:
                extracted_pages.append(text)
            else:
                scanned_pages.append(page_num)

        # 2. Second pass: for pages with little or no digital text, render to image and run OCR
        if scanned_pages:
            logger.info(f"PDF has {len(scanned_pages)} scanned page(s). Running OCR...")
            for page_num in scanned_pages:
                page = doc[page_num]
                # Render page at 200 DPI for high OCR accuracy
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                ocr_text = await self._run_ocr_on_pil_image(img)
                if ocr_text.strip():
                    extracted_pages.append(ocr_text.strip())

        doc.close()
        return "\n\n".join(extracted_pages).strip()

    async def extract_text_from_image(self, file_bytes: bytes) -> str:
        """Extract text from photo or scanned image using OCR."""
        try:
            image = Image.open(io.BytesIO(file_bytes))
            # Convert to RGB if palette/RGBA
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
        except Exception as e:
            raise ValueError(f"Failed to read image file: {e}")

        text = await self._run_ocr_on_pil_image(image)
        return text.strip()

    async def _run_ocr_on_pil_image(self, image: Image.Image) -> str:
        """Execute OCR on a PIL image using native Windows OCR with fallbacks."""
        # 1. Native Windows Media OCR (high performance, built into Windows 10/11)
        if self.winocr_available:
            try:
                # Resize if image is extremely large to avoid memory constraints
                max_dim = 2500
                if max(image.size) > max_dim:
                    scale = max_dim / max(image.size)
                    new_size = (int(image.size[0] * scale), int(image.size[1] * scale))
                    image = image.resize(new_size, Image.Resampling.LANCZOS)

                result = await winocr.recognize_pil(image, lang="en-US")
                if result and result.text:
                    return result.text
            except Exception as e:
                logger.warning(f"winocr recognition failed: {e}. Checking fallback OCR...")

        # 2. Optional pytesseract fallback
        if self.pytesseract_available:
            try:
                loop = asyncio.get_event_loop()
                text = await loop.run_in_executor(None, pytesseract.image_to_string, image)
                if text and text.strip():
                    return text
            except Exception as e:
                logger.warning(f"pytesseract fallback failed: {e}")

        if not self.winocr_available and not self.pytesseract_available:
            raise RuntimeError(
                "No OCR engine available. Please install 'winocr' on Windows or configure Tesseract."
            )

        return ""

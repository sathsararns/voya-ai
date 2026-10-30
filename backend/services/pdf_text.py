"""Extract PDFs, with fingerprinted reviewed text or optional local OCR.

Sidecars are JSON and never ingested as separate knowledge documents. A
changed PDF invalidates its sidecar instead of silently using stale text.
"""

from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import os

from pypdf import PdfReader


def read_pdf_pages(path: str) -> list[dict]:
    pdf_path = Path(path)
    sidecar = pdf_path.with_suffix(pdf_path.suffix + ".text.json")
    if sidecar.exists():
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if data.get("pdf_sha256") != sha256(pdf_path.read_bytes()).hexdigest():
            raise ValueError(f"Reviewed text no longer matches {pdf_path.name}; regenerate it before ingestion.")
        pages = data.get("pages", [])
        if not pages or any(not isinstance(p.get("text"), str) or not p["text"].strip() for p in pages):
            raise ValueError("Reviewed PDF text contains empty or invalid pages.")
        return [{**p, "extraction_method": "reviewed_transcription",
                 "source_date": data.get("source_date")} for p in pages]

    pages = [{"page_number": n, "text": page.extract_text() or "",
              "extraction_method": "pdf_text"}
             for n, page in enumerate(PdfReader(path).pages, 1)]
    missing = [p["page_number"] for p in pages if not p["text"].strip()]
    if missing:
        ocr_pages = ocr_pdf_pages(pdf_path, missing)
        for page in pages:
            if page["page_number"] in ocr_pages:
                page.update(text=ocr_pages[page["page_number"]], extraction_method="ocr_unverified")
    if any(not p["text"].strip() for p in pages):
        raise ValueError(f"{pdf_path.name}: OCR produced an empty page; previous knowledge was preserved.")
    return pages


def ocr_pdf_pages(path: Path, page_numbers: list[int]) -> dict[int, str]:
    """No remote uploads: Windows built-in OCR or Poppler + Tesseract."""
    if os.name == "nt":
        script = Path(__file__).resolve().parents[1] / "scripts" / "ocr_pdf_windows.ps1"
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-PdfPath", str(path.resolve()),
             "-PageNumbers", ",".join(map(str, page_numbers))],
            capture_output=True, text=True, encoding="utf-8", timeout=180, check=True,
        )
        data = json.loads(result.stdout.lstrip("\ufeff"))
        return {int(p["page_number"]): p["text"] for p in data}
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        raise RuntimeError("Scanned PDF needs local OCR: install poppler-utils and tesseract-ocr, "
                           "or provide a reviewed .pdf.text.json sidecar. Previous knowledge was preserved.")
    output = {}
    with tempfile.TemporaryDirectory(prefix="voya-ocr-") as directory:
        for number in page_numbers:
            prefix = str(Path(directory) / f"page-{number}")
            subprocess.run(["pdftoppm", "-f", str(number), "-l", str(number), "-r", "220",
                            "-singlefile", "-png", str(path.resolve()), prefix],
                           check=True, capture_output=True, timeout=90)
            result = subprocess.run(["tesseract", prefix + ".png", "stdout", "-l", "eng"],
                                    check=True, capture_output=True, text=True, encoding="utf-8", timeout=90)
            output[number] = result.stdout
    return output

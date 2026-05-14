"""
Production HTTP service for the Neptune Benson submittal generator.

Deployed on Render (or similar) and called from n8n Cloud.

Endpoints:
  GET  /health        — liveness check for Render
  POST /generate      — accepts a quote PDF, returns the submittal PDF

Authentication: a shared secret header (X-API-Key) protects /generate.
The key is read from the API_KEY environment variable.
"""
import os
import shutil
import tempfile
import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse

from orchestrator import generate_submittal


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("API_KEY", "")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB — quotes are ~1MB, give headroom

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("submittal_service")


app = FastAPI(
    title="Neptune Benson Submittal Generator",
    description="Converts Evoqua quote PDFs into annotated submittal packages",
    version="1.0.0",
)


@app.get("/health")
def health():
    """Liveness check used by Render to confirm the service is up."""
    return {"status": "ok"}


@app.post("/generate")
async def generate(
    quote_pdf: UploadFile = File(...),
    job_number: str = Form(""),
    engineer_initials: str = Form(""),
    x_api_key: str = Header(default=""),
):
    # ----- Authentication -----
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key header")

    # ----- Input validation -----
    if not quote_pdf.filename or not quote_pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "quote_pdf must be a PDF file")

    workdir = Path(tempfile.mkdtemp(prefix="submittal_"))
    logger.info(
        "Generate request: file=%s job=%s initials=%s",
        quote_pdf.filename, job_number, engineer_initials,
    )

    try:
        # Save upload and check size
        quote_path = workdir / "quote.pdf"
        with quote_path.open("wb") as f:
            total = 0
            while chunk := await quote_pdf.read(64 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Quote PDF exceeds 10MB limit")
                f.write(chunk)

        # Run the pipeline
        output_path = workdir / "submittal.pdf"
        generate_submittal(
            str(quote_path),
            str(output_path),
            job_number=job_number,
            engineer_initials=engineer_initials,
        )

        if not output_path.exists():
            raise HTTPException(500, "Generation produced no output")

        # Stream the result back to the caller
        return FileResponse(
            str(output_path),
            media_type="application/pdf",
            filename=f"submittal_{Path(quote_pdf.filename).stem}.pdf",
            # Render will clean up workdir after response is sent
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Generation failed")
        raise HTTPException(500, f"Generation failed: {type(e).__name__}: {e}")

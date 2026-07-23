"""
Production HTTP service for the Neptune Benson submittal generator.

Deployed on Render (or similar) and called from n8n Cloud.

Endpoints:
  GET  /health        — liveness check for Render
  POST /generate      — accepts a quote PDF, returns the submittal PDF

Authentication: a shared secret header (X-API-Key) protects /generate.
The key is read from the API_KEY environment variable.

Concurrency model (Jul 2026 fix): generate_submittal() is CPU-bound and was
previously called directly from the async endpoint, which froze the event
loop for the entire run. Render's /health probes (5s timeout) went dark and
the instance got killed mid-request. Generation now runs in a separate
worker process via ProcessPoolExecutor, so the main process always answers
/health no matter how heavy the pipeline gets.
"""
import asyncio
import functools
import os
import re
import shutil
import tempfile
import logging
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

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
    version="1.1.0",
)

# One worker process, one job at a time. Keeps peak memory at a single
# pipeline run (~700MB-1.1GB per render.yaml notes) on the 2GB plan while
# leaving the main process free to serve /health. A second concurrent
# /generate call queues behind the first instead of doubling memory.
_executor: ProcessPoolExecutor | None = None


@app.on_event("startup")
def _start_executor():
    global _executor
    _executor = ProcessPoolExecutor(max_workers=1)


@app.on_event("shutdown")
def _stop_executor():
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Date format conversion
# ---------------------------------------------------------------------------
# The n8n Form Trigger date picker emits ISO format (YYYY-MM-DD). The cover
# page template's placeholder is MM/DD/YY (US two-digit year). We accept
# either format from the caller and normalize to MM/DD/YY for the cover
# page filler. This keeps the API tolerant of test calls via curl that
# pass the US format directly.
#
# As of Jul 2026 the n8n form no longer collects a return date, so the
# field is optional; empty input normalizes to "".
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_US_DATE_2 = re.compile(r"^(\d{2})/(\d{2})/(\d{2})$")
_US_DATE_4 = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _normalize_date_for_cover(raw: str) -> str:
    """Convert ISO YYYY-MM-DD or MM/DD/YYYY to MM/DD/YY. Pass through MM/DD/YY.

    Raises ValueError on unrecognized formats so the API can return 400.
    """
    s = (raw or "").strip()
    if not s:
        return ""

    m = _ISO_DATE.match(s)
    if m:
        yyyy, mm, dd = m.groups()
        return f"{mm}/{dd}/{yyyy[2:]}"

    m = _US_DATE_2.match(s)
    if m:
        return s  # already in target format

    m = _US_DATE_4.match(s)
    if m:
        mm, dd, yyyy = m.groups()
        return f"{mm}/{dd}/{yyyy[2:]}"

    raise ValueError(
        f"Unrecognized date format: {s!r}. "
        f"Expected YYYY-MM-DD, MM/DD/YY, or MM/DD/YYYY."
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
    submittal_return_date: str = Form(""),  # optional since Jul 2026 form change
    project_name: str = Form(""),  # optional override of the quote-parsed name
    x_api_key: str = Header(default=""),
):
    # ----- Authentication -----
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key header")

    # ----- Input validation -----
    if not quote_pdf.filename or not quote_pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "quote_pdf must be a PDF file")

    try:
        normalized_return_date = _normalize_date_for_cover(submittal_return_date)
    except ValueError as e:
        raise HTTPException(400, str(e))

    workdir = Path(tempfile.mkdtemp(prefix="submittal_"))
    logger.info(
        "Generate request: file=%s job=%s initials=%s project_name=%s "
        "return_date=%s (normalized: %s)",
        quote_pdf.filename, job_number, engineer_initials, project_name,
        submittal_return_date, normalized_return_date,
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

        # Run the pipeline in the worker process so the event loop stays
        # free to answer /health while WeasyPrint/OCR churn.
        output_path = workdir / "submittal.pdf"
        job = functools.partial(
            generate_submittal,
            str(quote_path),
            str(output_path),
            job_number=job_number,
            engineer_initials=engineer_initials,
            submittal_return_date=normalized_return_date,
            project_name=project_name.strip(),
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_executor, job)

        if not output_path.exists():
            raise HTTPException(500, "Generation produced no output")

        # Stream the result back to the caller, then delete the per-request
        # workdir once the response has finished sending. Render does NOT clean
        # /tmp, so without this the quote + raw + final PDFs leak every request.
        return FileResponse(
            str(output_path),
            media_type="application/pdf",
            filename=f"submittal_{Path(quote_pdf.filename).stem}.pdf",
            background=BackgroundTask(shutil.rmtree, str(workdir), ignore_errors=True),
        )

    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        logger.exception("Generation failed")
        raise HTTPException(500, f"Generation failed: {type(e).__name__}: {e}")

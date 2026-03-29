import os
import threading
import uuid as _uuid

import boto3
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from utils.k_bases import process_and_upload_image, trigger_kb_sync

router = APIRouter(prefix="/catalog")

S3_IMAGE_BUCKET = os.getenv("S3_IMAGE_BUCKET", "imagenes-generales-018787439815")
_INDEX = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))
_s3 = boto3.client("s3")

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# --- In-memory stores ---

_jobs: dict[str, dict] = {}
_items: list[dict] = []          # successfully uploaded items (session-scoped)
_lock = threading.Lock()


def _set_job(job_id: str, **kwargs):
    with _lock:
        _jobs.setdefault(job_id, {}).update(kwargs)


def _get_job(job_id: str) -> dict | None:
    with _lock:
        return dict(_jobs[job_id]) if job_id in _jobs else None


def _add_item(s3_url: str, caption: dict):
    with _lock:
        _items.insert(0, {"s3_url": s3_url, "caption": caption})


def _presign(s3_url: str, expires: int = 3600) -> str:
    # s3_url format: s3://bucket/key
    path = s3_url.removeprefix("s3://")
    bucket, key = path.split("/", 1)
    return _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


# --- Background task ---

def _process_upload(job_id: str, image_bytes: bytes, s3_key: str, content_type: str):
    try:
        result = process_and_upload_image(
            image_bytes=image_bytes,
            bucket_name=S3_IMAGE_BUCKET,
            s3_key=s3_key,
            content_type=content_type,
        )
        sync = trigger_kb_sync(description=f"Web upload: {s3_key}")
        caption = result["metadata"]
        s3_url = result["s3_url"]
        _add_item(s3_url, caption)
        _set_job(
            job_id,
            status="done",
            caption=caption,
            s3_url=s3_url,
            ingestion_job_id=sync.get("ingestion_job_id"),
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _set_job(job_id, status="error", error=str(exc))


# --- Routes ---

@router.get("/", include_in_schema=False)
def catalog_page():
    return FileResponse(_INDEX)


@router.post("/upload")
async def upload_catalog_item(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    content_type = (file.content_type or "").lower()
    if content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Solo se aceptan imágenes JPEG, PNG o WebP.")

    image_bytes = await file.read()
    if len(image_bytes) == 0:
        raise HTTPException(status_code=400, detail="Archivo vacío.")
    if len(image_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Archivo demasiado grande. Máximo 10 MB.")

    ext = {"image/png": "png", "image/webp": "webp"}.get(content_type, "jpg")
    original_name = (file.filename or "upload").rsplit(".", 1)[0]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in original_name)[:40]
    s3_key = f"prendas/{safe_name}_{_uuid.uuid4().hex[:8]}.{ext}"

    job_id = _uuid.uuid4().hex
    _set_job(job_id, status="processing")

    background_tasks.add_task(_process_upload, job_id, image_bytes, s3_key, content_type)

    return JSONResponse({"job_id": job_id, "status": "processing"}, status_code=202)


@router.get("/status/{job_id}")
def job_status(job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado.")
    # Attach presigned URL if done
    if job.get("status") == "done" and job.get("s3_url"):
        job = dict(job)
        try:
            job["image_url"] = _presign(job["s3_url"])
        except Exception:
            pass
    return JSONResponse(job)


@router.get("/items")
def list_items():
    with _lock:
        snapshot = list(_items)
    result = []
    for item in snapshot:
        try:
            url = _presign(item["s3_url"])
            result.append({"image_url": url, "caption": item["caption"]})
        except Exception:
            pass
    return JSONResponse(result)

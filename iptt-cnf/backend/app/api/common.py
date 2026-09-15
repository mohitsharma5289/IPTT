"""Shared helpers for the HTTP layer."""
from __future__ import annotations

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Project

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MEDIA_TYPE = "application/pdf"


def require_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def safe_filename(value: str, limit: int = 60) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)[:limit]


def attachment(content: bytes, filename: str, media_type: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def xlsx_response(content: bytes, filename: str) -> StreamingResponse:
    return attachment(content, filename, XLSX_MEDIA_TYPE)


def pdf_response(content: bytes, filename: str) -> StreamingResponse:
    return attachment(content, filename, PDF_MEDIA_TYPE)


async def read_upload(file: UploadFile, *, extension: str = ".xlsx") -> bytes:
    """Read an upload under a hard size cap.

    The legacy importers handed the file straight to pandas with no bound, so a
    single oversized workbook was an OOMKill under a container memory limit
    (audit M12).
    """
    settings = get_settings()
    if file.filename and not file.filename.lower().endswith(extension):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Upload a {extension} file"
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"That file exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit",
            )
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty")
    return b"".join(chunks)

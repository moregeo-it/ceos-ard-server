import hashlib
import logging
import os
from email.utils import formatdate
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_preview_service
from app.schemas.error import create_error_detail
from app.services.auth_service import require_github_user
from app.services.preview_service import PreviewService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Previews"])


def _compute_file_etag(stat_result: os.stat_result) -> str:
    """Build an ETag validator from the file's size and modification time.

    Computed up front (before reading the body) so conditional requests can be answered
    with a 304. A deleted/replaced file yields a different validator (or a 404), so a stale
    asset can never satisfy a revalidation.
    """
    base = f"{stat_result.st_mtime_ns}-{stat_result.st_size}"
    return f'"{hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()}"'


def _if_none_match_matches(if_none_match: str, etag: str) -> bool:
    """Return True if the client's If-None-Match header covers the current ETag."""
    if if_none_match.strip() == "*":
        return True

    def _normalize(tag: str) -> str:
        tag = tag.strip()
        # Ignore the weak-validator prefix when comparing.
        return tag[2:] if tag.startswith("W/") else tag

    normalized_etag = _normalize(etag)
    return any(_normalize(candidate) == normalized_etag for candidate in if_none_match.split(","))


@router.get(
    "/{workspace_id}/previews",
    summary="Generate Previews",
    description="Generate Previews for a workspace",
    status_code=status.HTTP_200_OK,
)
async def generate_preview(
    workspace_id: str,
    db: Session = Depends(get_db),
    pfs: list[str] | None = Query(default=None, min_items=1, max_items=50),
    current_user: dict[str, Any] = Depends(require_github_user),
    preview_service: PreviewService = Depends(get_preview_service),
):
    try:
        generated_previews = await preview_service.generate_preview(db=db, pfs=pfs, workspace_id=workspace_id, user_id=current_user["user"].id)

        # Preview HTML is regenerated on every request; never let the browser cache it.
        return Response(
            content=generated_previews,
            status_code=status.HTTP_200_OK,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting preview for workspace {workspace_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("generate preview", e),
        ) from e


@router.get(
    "/{workspace_id}/previews/{file_path:path}",
    summary="Get preview static file asset",
    description="Get preview static file asset for a workspace",
    status_code=status.HTTP_200_OK,
)
async def get_preview_static_file(
    request: Request,
    workspace_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    preview_service: PreviewService = Depends(get_preview_service),
):
    try:
        # Raises 404 if the asset no longer exists (e.g. it was deleted).
        file = await preview_service.get_preview_static_file(db=db, file_path=file_path, workspace_id=workspace_id, user_id=current_user["user"].id)

        stat_result = file.stat()
        etag = _compute_file_etag(stat_result)
        cache_headers = {
            "ETag": etag,
            "Last-Modified": formatdate(stat_result.st_mtime, usegmt=True),
            # Cache, but always revalidate: the browser sends If-None-Match and we answer with
            # 304 (unchanged), 200 (changed) or 404 (deleted). This keeps revalidation cheap
            # while guaranteeing a stale asset is never served.
            "Cache-Control": "no-cache",
        }

        if_none_match = request.headers.get("if-none-match")
        if if_none_match and _if_none_match_matches(if_none_match, etag):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)

        return FileResponse(str(file), headers=cache_headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting preview static file {file_path} for workspace {workspace_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("get preview static file", e),
        ) from e


@router.get(
    "/{workspace_id}/download",
    summary="Download Previews PDF Document or DOCX",
    description="Download Previews PDF Document or DOCX for a workspace",
    status_code=status.HTTP_200_OK,
)
async def download_preview_document(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    preview_service: PreviewService = Depends(get_preview_service),
    format: str = Query(..., enum=["pdf", "docx"]),
    pfs: list[str] = Query(min_items=1, max_items=50),
):
    media_types = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    media_type = media_types.get(format, "application/octet-stream")
    try:
        document_file = await preview_service.download_preview_document(
            db=db, pfs=pfs, format=format, workspace_id=workspace_id, user_id=current_user["user"].id
        )

        return FileResponse(
            path=document_file["path"],
            filename=document_file["name"],
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={document_file['name']}"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading preview document for workspace {workspace_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("download preview document", e),
        ) from e

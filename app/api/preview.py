import logging
from email.utils import formatdate
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_preview_service
from app.services.auth_service import require_github_user
from app.services.preview_service import PreviewService
from app.utils.http_utils import compute_file_etag, if_none_match_matches, internal_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Previews"])


@router.post(
    "/{workspace_id}/previews",
    summary="Generate Previews",
    description="Generate the preview for a workspace (owner only); everyone else sees this build",
    status_code=status.HTTP_200_OK,
)
async def generate_preview(
    workspace_id: str,
    db: Session = Depends(get_db),
    pfs: list[str] | None = Query(default=None, min_items=1, max_items=50),
    current_user: dict[str, Any] = Depends(require_github_user),
    preview_service: PreviewService = Depends(get_preview_service),
):
    with internal_errors("generate preview", logger):
        generated_previews = await preview_service.generate_preview(db=db, pfs=pfs, workspace_id=workspace_id, user_id=current_user["user"].id)

        # Preview HTML is regenerated on every request; never let the browser cache it.
        return Response(
            content=generated_previews,
            status_code=status.HTTP_200_OK,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )


@router.get(
    "/{workspace_id}/previews/current",
    summary="Get the current preview",
    description="The owner's last generated preview for the workspace's PFS list, without building",
    status_code=status.HTTP_200_OK,
)
async def get_current_preview(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    preview_service: PreviewService = Depends(get_preview_service),
):
    with internal_errors("get current preview", logger):
        html = await preview_service.get_current_preview(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)
        return Response(content=html, status_code=status.HTTP_200_OK, media_type="text/html", headers={"Cache-Control": "no-store"})


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
    with internal_errors("get preview static file", logger):
        # Raises 404 if the asset no longer exists (e.g. it was deleted).
        file = await preview_service.get_preview_static_file(db=db, file_path=file_path, workspace_id=workspace_id, user_id=current_user["user"].id)

        stat_result = file.stat()
        etag = compute_file_etag(stat_result)
        cache_headers = {
            "ETag": etag,
            "Last-Modified": formatdate(stat_result.st_mtime, usegmt=True),
            # Cache, but always revalidate: the browser sends If-None-Match and we answer with
            # 304 (unchanged), 200 (changed) or 404 (deleted). This keeps revalidation cheap
            # while guaranteeing a stale asset is never served.
            "Cache-Control": "no-cache",
        }

        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match_matches(if_none_match, etag):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)

        return FileResponse(str(file), headers=cache_headers)


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
    with internal_errors("download preview document", logger):
        document_file = await preview_service.download_preview_document(
            db=db, pfs=pfs, format=format, workspace_id=workspace_id, user_id=current_user["user"].id
        )

        return FileResponse(
            path=document_file["path"],
            filename=document_file["name"],
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={document_file['name']}"},
        )

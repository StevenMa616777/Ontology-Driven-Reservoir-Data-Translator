"""Internal routes used only by the same-origin OCR Lab page."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .models import CropRunRequest
from .service import CropLabError, CropLabService


def create_ocr_lab_router(
    service: CropLabService,
    *,
    execution_lock: asyncio.Lock,
) -> APIRouter:
    router = APIRouter(prefix="/api/ocr-lab", tags=["internal-ocr-lab"])

    @router.get("/engines", include_in_schema=False)
    def engines() -> dict:
        return {"engines": service.engines(), "default_engine": None}

    @router.post("/runs", include_in_schema=False)
    async def run_crop(request: CropRunRequest) -> dict:
        try:
            async with execution_lock:
                return await asyncio.to_thread(service.run, request)
        except CropLabError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    @router.get("/runs/{run_id}/artifacts/{kind}", include_in_schema=False)
    def artifact(run_id: UUID, kind: str) -> FileResponse:
        try:
            path = service.artifact(str(run_id), kind)
        except CropLabError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        media_type = "application/json" if kind == "result" else "image/png"
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})

    return router

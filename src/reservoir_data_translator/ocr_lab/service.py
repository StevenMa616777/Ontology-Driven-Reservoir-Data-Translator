"""Crop persistence, preprocessing, and direct OCR-core execution."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, is_dataclass
from hashlib import sha256
from importlib.util import find_spec
from io import BytesIO
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from reservoir_data_translator.ingestion import PaddleOcrBackend
from reservoir_data_translator.ingestion.ocr import (
    CropPipelineSelection,
    CropRecognitionPipeline,
    CropRunContext,
    EngineDescriptor,
    OcrEngineRegistry,
    PaddleCompositeCropEngine,
    register_paddle_components,
)

from .models import CropPreprocessing, CropRunRequest


MAX_CROP_BYTES = 20 * 1024 * 1024
MAX_CROP_PIXELS = 40_000_000
MAX_PROCESSED_CROP_PIXELS = 40_000_000
MEDIA_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
}


class CropLabError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def build_default_engine_registry() -> OcrEngineRegistry:
    """Register installed engines without selecting an implicit default."""

    registry = OcrEngineRegistry()
    paddle_available = find_spec("paddleocr") is not None

    def paddle_factory() -> PaddleCompositeCropEngine:
        languages = tuple(
            item.strip()
            for item in os.getenv("RESERVOIR_OCR_LANGUAGES", "ch,en").split(",")
            if item.strip()
        ) or ("ch", "en")
        backend = PaddleOcrBackend(
            lang=os.getenv("RESERVOIR_OCR_LANG") or languages[0],
            device=os.getenv("RESERVOIR_OCR_DEVICE", "gpu:0").strip() or "gpu:0",
            paddlex_config=os.getenv("RESERVOIR_OCR_PADDLEX_CONFIG") or None,
            minimum_confidence=float(os.getenv("RESERVOIR_OCR_MIN_CONFIDENCE", "0.70")),
            enable_mkldnn=(
                os.getenv("RESERVOIR_OCR_ENABLE_MKLDNN", "false").strip().casefold()
                in {"1", "true", "yes", "on"}
            ),
            cpu_threads=(
                int(os.environ["RESERVOIR_OCR_CPU_THREADS"])
                if os.getenv("RESERVOIR_OCR_CPU_THREADS")
                else None
            ),
        )
        return PaddleCompositeCropEngine(backend)

    registry.register(
        EngineDescriptor(
            engine_id="paddle-ppstructure-v3",
            display_name="Current PP-StructureV3 baseline",
            kind="composite",
            available=paddle_available,
            unavailable_reason=(
                None if paddle_available else "PaddleOCR optional dependencies are not installed"
            ),
            description=(
                "现有整套版面、文字与表格流程，仅作为显式选择的对照基线；"
                "其内部组件不能在 OCR Lab 中独立替换。"
            ),
        ),
        paddle_factory,
    )
    register_paddle_components(
        registry,
        device=os.getenv("RESERVOIR_OCR_DEVICE", "gpu:0").strip() or "gpu:0",
        layout_model=os.getenv("RESERVOIR_OCR_LAYOUT_MODEL", "PP-DocLayout_plus-L"),
        text_detection_model=os.getenv("RESERVOIR_OCR_TEXT_DETECTION_MODEL", "PP-OCRv5_server_det"),
        text_recognition_model=os.getenv("RESERVOIR_OCR_TEXT_RECOGNITION_MODEL", "PP-OCRv5_server_rec"),
        subtable_model=os.getenv("RESERVOIR_OCR_SUBTABLE_MODEL", "PP-DocLayout_plus-L"),
    )
    return registry


class CropLabService:
    def __init__(
        self,
        registry: OcrEngineRegistry | None = None,
        *,
        artifact_root: Path | None = None,
    ) -> None:
        self.registry = registry or build_default_engine_registry()
        self.pipeline = CropRecognitionPipeline(self.registry)
        self.artifact_root = artifact_root or (
            Path(__file__).resolve().parents[3] / "tmp" / "ocr_lab" / "runs"
        )

    def engines(self) -> list[dict[str, Any]]:
        return [asdict(descriptor) for descriptor in self.registry.descriptors()]

    @staticmethod
    def _decode(payload: str) -> bytes:
        value = payload.split(",", 1)[1] if payload.startswith("data:") and "," in payload else payload
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CropLabError("OCR_LAB_IMAGE_INVALID", "Crop image is not valid base64.") from exc
        if not decoded:
            raise CropLabError("OCR_LAB_IMAGE_EMPTY", "Crop image is empty.")
        if len(decoded) > MAX_CROP_BYTES:
            raise CropLabError(
                "OCR_LAB_IMAGE_TOO_LARGE",
                f"Crop image exceeds the {MAX_CROP_BYTES}-byte limit.",
                status_code=413,
            )
        return decoded

    @staticmethod
    def _open_image(payload: bytes) -> Image.Image:
        try:
            image = Image.open(BytesIO(payload))
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_CROP_PIXELS:
                raise CropLabError(
                    "OCR_LAB_PIXEL_LIMIT_EXCEEDED",
                    f"Crop dimensions {width}x{height} exceed the pixel budget.",
                )
            image.load()
            return image.convert("RGB")
        except CropLabError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise CropLabError("OCR_LAB_IMAGE_INVALID", "Crop content is not a supported image.") from exc

    @staticmethod
    def _crop(image: Image.Image, request: CropRunRequest) -> tuple[Image.Image, dict[str, int]]:
        if request.bbox is None:
            return image.copy(), {"x0": 0, "y0": 0, "x1": image.width, "y1": image.height}
        box = request.bbox
        padding = request.preprocessing.padding
        x0 = max(0, box.x0 - padding)
        y0 = max(0, box.y0 - padding)
        x1 = min(image.width, box.x1 + padding)
        y1 = min(image.height, box.y1 + padding)
        if x1 <= x0 or y1 <= y0 or box.x1 > image.width or box.y1 > image.height:
            raise CropLabError(
                "OCR_LAB_BBOX_INVALID",
                "Crop bounding box must be non-empty and remain inside the source image.",
            )
        return image.crop((x0, y0, x1, y1)), {"x0": x0, "y0": y0, "x1": x1, "y1": y1}

    @staticmethod
    def _preprocess(image: Image.Image, settings: CropPreprocessing) -> Image.Image:
        result = image.copy()
        if settings.scale != 1.0:
            result = result.resize(
                (
                    max(1, round(result.width * settings.scale)),
                    max(1, round(result.height * settings.scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        if settings.grayscale or settings.threshold is not None:
            result = ImageOps.grayscale(result)
        if settings.contrast != 1.0:
            result = ImageEnhance.Contrast(result).enhance(settings.contrast)
        if settings.sharpen:
            for _ in range(max(1, round(settings.sharpen))):
                result = result.filter(ImageFilter.SHARPEN)
        if settings.threshold is not None:
            threshold = settings.threshold
            result = result.point(lambda value: 255 if value >= threshold else 0)
        return result.convert("RGB")

    @staticmethod
    def _validate_processed_size(image: Image.Image, settings: CropPreprocessing) -> None:
        width = max(1, round(image.width * settings.scale))
        height = max(1, round(image.height * settings.scale))
        if width * height > MAX_PROCESSED_CROP_PIXELS:
            raise CropLabError(
                "OCR_LAB_PROCESSED_PIXEL_LIMIT_EXCEEDED",
                (
                    f"Processed crop dimensions {width}x{height} exceed the "
                    f"{MAX_PROCESSED_CROP_PIXELS}-pixel budget."
                ),
                status_code=413,
            )

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if is_dataclass(value):
            return {key: CropLabService._jsonable(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {str(key): CropLabService._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [CropLabService._jsonable(item) for item in value]
        return value

    def run(self, request: CropRunRequest) -> dict[str, Any]:
        run_id = str(uuid4())
        payload = self._decode(request.content_base64)
        original = self._open_image(payload)
        crop: Image.Image | None = None
        processed: Image.Image | None = None
        try:
            crop, effective_bbox = self._crop(original, request)
            self._validate_processed_size(crop, request.preprocessing)
            processed = self._preprocess(crop, request.preprocessing)
            run_dir = (self.artifact_root / run_id).resolve()
            root = self.artifact_root.resolve()
            if run_dir.parent != root:
                raise CropLabError("OCR_LAB_STORAGE_INVALID", "Invalid OCR Lab run path.")
            run_dir.mkdir(parents=True, exist_ok=False)
            suffix = MEDIA_SUFFIX[request.media_type]
            (run_dir / f"original{suffix}").write_bytes(payload)
            crop.save(run_dir / "crop.png", format="PNG")
            processed.save(run_dir / "processed.png", format="PNG")

            selection = CropPipelineSelection(**request.pipeline.model_dump())
            context = CropRunContext(
                run_id=run_id,
                crop_kind=request.crop_kind,
                languages=tuple(request.languages),
                parameters=request.preprocessing.model_dump(),
            )
            started = perf_counter()
            try:
                result = self.pipeline.run(
                    processed,
                    selection=selection,
                    context=context,
                )
            except (KeyError, ValueError, RuntimeError) as exc:
                raise CropLabError("OCR_LAB_ENGINE_INVALID", str(exc)) from exc
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            response = {
                "run_id": run_id,
                "status": "completed",
                "input": {
                    "file_name": Path(request.file_name).name,
                    "media_type": request.media_type,
                    "sha256": sha256(payload).hexdigest(),
                    "source_width": original.width,
                    "source_height": original.height,
                    "crop_width": crop.width,
                    "crop_height": crop.height,
                    "processed_width": processed.width,
                    "processed_height": processed.height,
                    "effective_bbox": effective_bbox,
                    "crop_kind": request.crop_kind,
                    "provenance": request.provenance.model_dump(mode="json"),
                },
                "pipeline": request.pipeline.model_dump(mode="json"),
                "preprocessing": request.preprocessing.model_dump(mode="json"),
                "expected_text": request.expected_text,
                "notes": request.notes,
                "duration_ms": elapsed_ms,
                "result": self._jsonable(result),
                "artifacts": {
                    "crop_url": f"/api/ocr-lab/runs/{run_id}/artifacts/crop",
                    "processed_url": f"/api/ocr-lab/runs/{run_id}/artifacts/processed",
                },
            }
            (run_dir / "result.json").write_text(
                json.dumps(response, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return response
        finally:
            original.close()
            if crop is not None:
                crop.close()
            if processed is not None:
                processed.close()

    def artifact(self, run_id: str, kind: str) -> Path:
        if kind not in {"crop", "processed", "result"}:
            raise CropLabError("OCR_LAB_ARTIFACT_NOT_FOUND", "Unknown OCR Lab artifact.", status_code=404)
        root = self.artifact_root.resolve()
        run_dir = (root / run_id).resolve()
        if run_dir.parent != root:
            raise CropLabError("OCR_LAB_ARTIFACT_NOT_FOUND", "OCR Lab run does not exist.", status_code=404)
        name = {"crop": "crop.png", "processed": "processed.png", "result": "result.json"}[kind]
        path = (run_dir / name).resolve()
        if path.parent != run_dir or not path.is_file():
            raise CropLabError("OCR_LAB_ARTIFACT_NOT_FOUND", "OCR Lab artifact does not exist.", status_code=404)
        return path

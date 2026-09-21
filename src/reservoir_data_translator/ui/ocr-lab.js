const elements = {
  file: document.querySelector("#crop-file"),
  dropzone: document.querySelector("#crop-dropzone"),
  sourceNote: document.querySelector("#crop-source-note"),
  editor: document.querySelector("#crop-editor"),
  viewport: document.querySelector("#crop-viewport"),
  stage: document.querySelector("#crop-stage"),
  image: document.querySelector("#crop-image"),
  sourceBoundary: document.querySelector("#crop-source-boundary"),
  selection: document.querySelector("#crop-selection"),
  dimensions: document.querySelector("#crop-dimensions"),
  clearSelection: document.querySelector("#clear-crop-selection"),
  cropKind: document.querySelector("#crop-kind"),
  expectedText: document.querySelector("#expected-text"),
  compositeConfig: document.querySelector("#composite-config"),
  componentConfig: document.querySelector("#component-config"),
  composite: document.querySelector("#composite-engine"),
  description: document.querySelector("#engine-description"),
  layout: document.querySelector("#layout-engine"),
  textDetection: document.querySelector("#text-detection-engine"),
  textRecognition: document.querySelector("#text-recognition-engine"),
  table: document.querySelector("#table-engine"),
  scale: document.querySelector("#pre-scale"),
  scaleOutput: document.querySelector("#pre-scale-output"),
  contrast: document.querySelector("#pre-contrast"),
  sharpen: document.querySelector("#pre-sharpen"),
  padding: document.querySelector("#pre-padding"),
  grayscale: document.querySelector("#pre-grayscale"),
  threshold: document.querySelector("#pre-threshold"),
  run: document.querySelector("#run-crop"),
  status: document.querySelector("#lab-status"),
  results: document.querySelector("#crop-run-results"),
};

const state = {
  file: null,
  objectUrl: null,
  bbox: null,
  dragStart: null,
  engines: [],
  provenance: { source_kind: "upload", original_flags: [], pixel_identical_to_original_ocr_input: true },
  sourceGeometry: null,
  previewFrame: null,
  runCount: 0,
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail?.message || `请求失败 (${response.status})`);
  return payload;
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.detail?.message || `运行失败 (${response.status})`);
    error.code = payload.detail?.code;
    throw error;
  }
  return payload;
}

function setStatus(message, error = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("is-error", error);
}

function updateRunAvailability() {
  const mode = document.querySelector('input[name="pipeline-mode"]:checked').value;
  const hasEngine = mode === "composite"
    ? Boolean(elements.composite.value)
    : [elements.layout, elements.textDetection, elements.textRecognition, elements.table].some(select => select.value);
  elements.run.disabled = !state.file || !hasEngine;
}

function showSourceNote() {
  const provenance = state.provenance;
  if (provenance.source_kind !== "ocr_review") {
    elements.sourceNote.hidden = true;
    return;
  }
  const confidence = provenance.original_confidence == null
    ? "置信度不可用"
    : `原置信度 ${Math.round(provenance.original_confidence * 100)}%`;
  elements.sourceNote.textContent = `来自低置信度 OCR 审查项 ${provenance.region_number || ""}；${confidence}。该图由中间 PDF 按原 OCR DPI 重建；1× 按原文 100% 逻辑尺度显示，并非原始内存像素。`;
  elements.sourceNote.hidden = false;
}

function setFile(file, provenance = null) {
  if (!file || !file.type.startsWith("image/")) {
    setStatus("请选择 PNG、JPEG、WebP 或 TIFF 图片。", true);
    return;
  }
  if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
  state.file = file;
  state.objectUrl = URL.createObjectURL(file);
  state.bbox = null;
  state.provenance = provenance || {
    source_kind: "upload",
    original_flags: [],
    pixel_identical_to_original_ocr_input: true,
  };
  state.sourceGeometry = state.provenance.source_geometry || null;
  elements.image.src = state.objectUrl;
  elements.editor.hidden = false;
  elements.selection.hidden = true;
  elements.dropzone.querySelector("strong").textContent = file.name;
  elements.dropzone.querySelector("span").textContent = `${Math.max(1, Math.round(file.size / 1024))} KB · 点击可更换`;
  showSourceNote();
  setStatus("");
  updateRunAvailability();
}

elements.image.addEventListener("load", () => {
  applyPreviewScale();
  renderSourceBoundary();
  renderSelection();
});

function previewScale() {
  return Number(elements.scale.value);
}

function previewBaseSize() {
  const geometry = state.sourceGeometry;
  const dpi = Number(geometry?.render_dpi);
  const factor = Number.isFinite(dpi) && dpi > 0 ? 96 / dpi : 1;
  return {
    width: elements.image.naturalWidth * factor,
    height: elements.image.naturalHeight * factor,
  };
}

function previewDisplaySize() {
  const base = previewBaseSize();
  const scale = previewScale();
  return { width: base.width * scale, height: base.height * scale };
}

function updateDimensionLabel() {
  if (!elements.image.naturalWidth) return;
  const display = previewDisplaySize();
  const scale = previewScale();
  const selection = state.bbox
    ? ` · 选区 ${state.bbox.x1 - state.bbox.x0} × ${state.bbox.y1 - state.bbox.y0} px · (${state.bbox.x0}, ${state.bbox.y0})–(${state.bbox.x1}, ${state.bbox.y1})`
    : " · 完整图片";
  elements.dimensions.textContent = `${elements.image.naturalWidth} × ${elements.image.naturalHeight} px${selection} · ${scale}× 预览 ${Math.round(display.width)} × ${Math.round(display.height)} CSS px`;
}

function applyPreviewScale() {
  if (!elements.image.naturalWidth) return;
  const scale = previewScale();
  const display = previewDisplaySize();
  elements.scaleOutput.value = `${scale}×`;
  elements.scale.setAttribute("aria-valuetext", `${scale}倍`);
  elements.image.style.width = `${display.width}px`;
  elements.image.style.height = `${display.height}px`;
  renderSelection();
}

function schedulePreviewScale() {
  elements.scaleOutput.value = `${previewScale()}×`;
  if (state.previewFrame !== null) cancelAnimationFrame(state.previewFrame);
  state.previewFrame = requestAnimationFrame(() => {
    state.previewFrame = null;
    applyPreviewScale();
  });
}

function renderSourceBoundary() {
  const box = state.sourceGeometry?.region_bbox_pixels;
  if (!box || !elements.image.naturalWidth || box.length !== 4) {
    elements.sourceBoundary.hidden = true;
    return;
  }
  const [x0, y0, x1, y1] = box;
  elements.sourceBoundary.style.left = `${x0 / elements.image.naturalWidth * 100}%`;
  elements.sourceBoundary.style.top = `${y0 / elements.image.naturalHeight * 100}%`;
  elements.sourceBoundary.style.width = `${(x1 - x0) / elements.image.naturalWidth * 100}%`;
  elements.sourceBoundary.style.height = `${(y1 - y0) / elements.image.naturalHeight * 100}%`;
  elements.sourceBoundary.hidden = false;
}

function imagePoint(event) {
  const rect = elements.image.getBoundingClientRect();
  const x = Math.max(0, Math.min(elements.image.naturalWidth,
    (event.clientX - rect.left) * elements.image.naturalWidth / rect.width));
  const y = Math.max(0, Math.min(elements.image.naturalHeight,
    (event.clientY - rect.top) * elements.image.naturalHeight / rect.height));
  return { x: Math.round(x), y: Math.round(y) };
}

function renderSelection() {
  if (!state.bbox || !elements.image.naturalWidth) {
    elements.selection.hidden = true;
    updateDimensionLabel();
    return;
  }
  const box = state.bbox;
  elements.selection.style.left = `${box.x0 / elements.image.naturalWidth * 100}%`;
  elements.selection.style.top = `${box.y0 / elements.image.naturalHeight * 100}%`;
  elements.selection.style.width = `${(box.x1 - box.x0) / elements.image.naturalWidth * 100}%`;
  elements.selection.style.height = `${(box.y1 - box.y0) / elements.image.naturalHeight * 100}%`;
  elements.selection.hidden = false;
  updateDimensionLabel();
}

elements.stage.addEventListener("pointerdown", event => {
  if (!state.file) return;
  elements.stage.setPointerCapture(event.pointerId);
  state.dragStart = imagePoint(event);
  state.bbox = { x0: state.dragStart.x, y0: state.dragStart.y, x1: state.dragStart.x, y1: state.dragStart.y };
  renderSelection();
});

elements.stage.addEventListener("pointermove", event => {
  if (!state.dragStart) return;
  const point = imagePoint(event);
  state.bbox = {
    x0: Math.min(state.dragStart.x, point.x),
    y0: Math.min(state.dragStart.y, point.y),
    x1: Math.max(state.dragStart.x, point.x),
    y1: Math.max(state.dragStart.y, point.y),
  };
  renderSelection();
});

function finishSelection() {
  if (!state.dragStart) return;
  state.dragStart = null;
  if (!state.bbox || state.bbox.x1 - state.bbox.x0 < 3 || state.bbox.y1 - state.bbox.y0 < 3) state.bbox = null;
  renderSelection();
}
elements.stage.addEventListener("pointerup", finishSelection);
elements.stage.addEventListener("pointercancel", finishSelection);
elements.clearSelection.addEventListener("click", () => { state.bbox = null; renderSelection(); });

function addEngineOptions(select, kind) {
  state.engines.filter(engine => engine.kind === kind).forEach(engine => {
    const option = document.createElement("option");
    option.value = engine.engine_id;
    option.textContent = `${engine.display_name}${engine.available ? "" : "（不可用）"}`;
    option.disabled = !engine.available;
    select.append(option);
  });
}

async function loadEngines() {
  try {
    const payload = await getJson("/api/ocr-lab/engines");
    state.engines = payload.engines;
    addEngineOptions(elements.composite, "composite");
    addEngineOptions(elements.layout, "layout");
    addEngineOptions(elements.textDetection, "text_detection");
    addEngineOptions(elements.textRecognition, "text_recognition");
    addEngineOptions(elements.table, "table");
  } catch (error) {
    setStatus(error.message, true);
  }
}

elements.composite.addEventListener("change", () => {
  const engine = state.engines.find(item => item.engine_id === elements.composite.value);
  elements.description.textContent = engine?.description || "";
  updateRunAvailability();
});
document.querySelectorAll('input[name="pipeline-mode"]').forEach(input => input.addEventListener("change", () => {
  const components = input.value === "components" && input.checked;
  if (input.checked) {
    elements.compositeConfig.hidden = components;
    elements.componentConfig.hidden = !components;
    updateRunAvailability();
  }
}));
[elements.layout, elements.textDetection, elements.textRecognition, elements.table].forEach(select => select.addEventListener("change", updateRunAvailability));
elements.scale.addEventListener("input", schedulePreviewScale);

function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(reader.error || new Error("无法读取图片"));
    reader.readAsDataURL(file);
  });
}

function pipelineRequest() {
  const mode = document.querySelector('input[name="pipeline-mode"]:checked').value;
  if (mode === "composite") return { composite_engine: elements.composite.value || null };
  return {
    layout_engine: elements.layout.value || null,
    text_detection_engine: elements.textDetection.value || null,
    text_recognition_engine: elements.textRecognition.value || null,
    table_engine: elements.table.value || null,
  };
}

function svgOverlay(pageResult) {
  if (!pageResult) return "";
  const regions = pageResult.regions || [];
  return `<svg viewBox="0 0 ${pageResult.image_width} ${pageResult.image_height}" aria-label="OCR 识别区域">${regions.map((region, index) => {
    const [x0, y0, x1, y1] = region.bbox_pixels;
    return `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"></rect><text x="${x0 + 3}" y="${Math.max(13, y0 + 13)}">R${index + 1} ${escapeHtml(region.layout_label)}</text>`;
  }).join("")}</svg>`;
}

function recognizedSummary(pageResult) {
  if (!pageResult) return "组件运行已完成；展开规范化结果查看各阶段输出。";
  return (pageResult.regions || []).map(region => {
    if (region.region_type === "table") return JSON.stringify({ columns: region.table?.columns || [], rows: region.table?.rows || [] }, null, 2);
    return region.text || `[${region.region_type}: ${region.layout_label}]`;
  }).filter(Boolean).join("\n\n") || "（没有可显示的识别内容）";
}

function renderRun(result) {
  if (state.runCount === 0) elements.results.innerHTML = "";
  state.runCount += 1;
  const pageResult = result.result.page_result;
  const engineNames = Object.values(result.pipeline).filter(Boolean).join(" + ");
  const card = document.createElement("article");
  card.className = "crop-run-card";
  card.innerHTML = `
    <header><div><span class="step-label">RUN ${String(state.runCount).padStart(2, "0")}</span><h3>${escapeHtml(engineNames)}</h3></div><small>${escapeHtml(result.duration_ms)} ms</small></header>
    <div class="crop-result-image"><img src="${escapeHtml(result.artifacts.processed_url)}" alt="本次 OCR 使用的处理后 Crop">${svgOverlay(pageResult)}</div>
    <div class="crop-run-meta"><span>${result.input.crop_width}×${result.input.crop_height} crop</span><span>${result.input.processed_width}×${result.input.processed_height} model input</span><span>scale ${escapeHtml(result.preprocessing.scale)}×</span><span>contrast ${escapeHtml(result.preprocessing.contrast)}</span><span>padding ${escapeHtml(result.preprocessing.padding)}</span></div>
    <pre class="crop-result-text">${escapeHtml(recognizedSummary(pageResult))}</pre>
    ${result.expected_text ? `<details><summary>查看期望文字</summary><pre>${escapeHtml(result.expected_text)}</pre></details>` : ""}
    <details><summary>查看规范化结果与阶段耗时</summary><pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre></details>`;
  elements.results.prepend(card);
}

async function runCrop() {
  if (!state.file) return;
  const threshold = elements.threshold.value === "" ? null : Number(elements.threshold.value);
  const provenance = { ...state.provenance };
  if (state.bbox) provenance.source_kind = "manual_recrop";
  const request = {
    file_name: state.file.name || "crop.png",
    media_type: state.file.type,
    content_base64: await fileBase64(state.file),
    crop_kind: elements.cropKind.value,
    bbox: state.bbox,
    languages: ["ch", "en"],
    expected_text: elements.expectedText.value || null,
    preprocessing: {
      scale: Number(elements.scale.value),
      grayscale: elements.grayscale.checked,
      contrast: Number(elements.contrast.value),
      sharpen: Number(elements.sharpen.value),
      threshold,
      padding: Number(elements.padding.value),
    },
    pipeline: pipelineRequest(),
    provenance,
  };
  elements.run.disabled = true;
  setStatus("正在运行所选模型；同一进程内不会与文件转换任务并发占用 OCR 资源。");
  try {
    const result = await postJson("/api/ocr-lab/runs", request);
    renderRun(result);
    setStatus(`运行完成：${result.run_id}`);
    document.querySelector(".crop-results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setStatus(`${error.code ? `${error.code}：` : ""}${error.message}`, true);
  } finally {
    updateRunAvailability();
  }
}
elements.run.addEventListener("click", runCrop);

elements.file.addEventListener("change", event => setFile(event.target.files[0] || null));
["dragenter", "dragover"].forEach(name => elements.dropzone.addEventListener(name, event => {
  event.preventDefault();
  elements.dropzone.classList.add("is-dragover");
}));
["dragleave", "drop"].forEach(name => elements.dropzone.addEventListener(name, event => {
  event.preventDefault();
  elements.dropzone.classList.remove("is-dragover");
}));
elements.dropzone.addEventListener("drop", event => setFile(event.dataTransfer.files[0] || null));
window.addEventListener("paste", event => {
  const item = [...(event.clipboardData?.items || [])].find(candidate => candidate.type.startsWith("image/"));
  if (!item) return;
  const file = item.getAsFile();
  if (file) setFile(new File([file], `clipboard-${Date.now()}.png`, { type: file.type || "image/png" }), {
    source_kind: "clipboard",
    original_flags: [],
    pixel_identical_to_original_ocr_input: true,
  });
});

async function loadReviewCrop() {
  const params = new URLSearchParams(location.search);
  const source = params.get("source");
  if (!source || !source.startsWith("/translation-jobs/")) return;
  try {
    const response = await fetch(source, { cache: "no-store" });
    if (!response.ok) throw new Error(`低置信度区域图像不可用 (${response.status})`);
    const geometryValue = response.headers.get("X-Reservoir-OCR-Geometry");
    const sourceGeometry = geometryValue ? JSON.parse(geometryValue) : null;
    const blob = await response.blob();
    const confidence = params.get("confidence");
    setFile(new File([blob], `${params.get("issue") || "ocr-review"}.png`, { type: blob.type || "image/png" }), {
      source_kind: "ocr_review",
      artifact_id: params.get("artifact") || null,
      issue_id: params.get("issue") || null,
      page: Number(params.get("page")) || null,
      region_number: params.get("region") || null,
      original_confidence: confidence === "" || confidence == null ? null : Number(confidence),
      original_flags: (params.get("flags") || "").split(",").filter(Boolean),
      pixel_identical_to_original_ocr_input: false,
      source_geometry: sourceGeometry,
    });
  } catch (error) {
    setStatus(error.message, true);
  }
}

loadEngines();
loadReviewCrop();

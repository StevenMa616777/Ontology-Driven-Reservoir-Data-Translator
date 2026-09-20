"use strict";

const MAX_SOURCE_BYTES = 16 * 1024 * 1024;
const TEXT_EXTENSIONS = new Set(["txt", "json", "csv"]);
const state = {
  file: null,
  result: null,
  running: false,
  translationFile: null,
};

const elements = {
  dropzone: document.querySelector("#dropzone"),
  fileInput: document.querySelector("#file-input"),
  fileSummary: document.querySelector("#file-summary"),
  fileName: document.querySelector("#file-name"),
  fileSize: document.querySelector("#file-size"),
  clearFile: document.querySelector("#clear-file"),
  sourceInput: document.querySelector("#source-input"),
  sourceSystem: document.querySelector("#source-system"),
  runButton: document.querySelector("#run-button"),
  resultRoot: document.querySelector("#result-root"),
  railSteps: [...document.querySelectorAll(".rail-step")],
};

class APIError extends Error {
  constructor(code, message, status, detail = {}) {
    super(message);
    this.name = "APIError";
    this.code = code;
    this.status = status;
    this.stage = detail.stage;
    this.stageLabel = detail.stage_label;
    this.resolution = detail.resolution;
    this.context = detail.context;
    this.deepseekTrace = detail.deepseek_trace;
  }
}

function escapeHtml(value) {
  return String(value ?? "—")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function selectedTarget() {
  return document.querySelector('input[name="target"]:checked').value;
}

function extensionFor(name) {
  return name.includes(".") ? name.split(".").pop().toLowerCase() : "txt";
}

function setSelectedFile(file) {
  if (!file) {
    state.file = null;
    elements.fileInput.value = "";
    elements.dropzone.classList.remove("has-file");
    elements.fileSummary.hidden = true;
    elements.sourceInput.disabled = false;
    elements.sourceInput.placeholder = "粘贴井控、PVT、SCAL 或者调度数据…";
    return;
  }
  if (file.size > MAX_SOURCE_BYTES) {
    renderError("SOURCE_TOO_LARGE", "文件超过 16 MB PoC 上限，请缩小后重试。");
    return;
  }
  if (![...TEXT_EXTENSIONS, "xlsx", "pdf"].includes(extensionFor(file.name))) {
    renderError("UNSUPPORTED_FILE", "当前仅支持 TXT、JSON、CSV、XLSX 和 PDF。");
    return;
  }
  state.file = file;
  elements.fileName.textContent = file.name;
  elements.fileSize.textContent = formatBytes(file.size);
  elements.dropzone.classList.add("has-file");
  elements.fileSummary.hidden = false;
  elements.sourceInput.value = "";
  elements.sourceInput.disabled = true;
  elements.sourceInput.placeholder = "已选择文件；移除文件后可以继续粘贴文本。";
}

async function fileToSource(file) {
  const extension = extensionFor(file.name);
  if (TEXT_EXTENSIONS.has(extension)) {
    return {
      file_name: file.name,
      content: await file.text(),
      content_encoding: "utf-8",
      source_id: file.name,
    };
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let start = 0; start < bytes.length; start += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(start, start + chunkSize));
  }
  return {
    file_name: file.name,
    content: btoa(binary),
    content_encoding: "base64",
    source_id: file.name,
  };
}

function openSelectedFile() {
  openLocalFile(state.file);
}

function openLocalFile(file) {
  if (!file) return;
  const extension = extensionFor(file.name);
  const previewTypes = {
    txt: "text/plain;charset=utf-8",
    json: "application/json;charset=utf-8",
    csv: "text/csv;charset=utf-8",
  };
  const previewFile = previewTypes[extension]
    ? new Blob([file], { type: previewTypes[extension] })
    : file;
  const objectUrl = URL.createObjectURL(previewFile);
  const opened = window.open(objectUrl, "_blank");
  if (!opened) {
    URL.revokeObjectURL(objectUrl);
    renderError("FILE_OPEN_BLOCKED", "浏览器阻止了新窗口，请允许此站点打开弹出窗口后重试。");
    return;
  }
  opened.opener = null;
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload?.detail;
    throw new APIError(
      detail?.code || `HTTP_${response.status}`,
      detail?.message || "转换服务返回了无法解析的错误。",
      response.status,
      detail || {},
    );
  }
  return payload;
}

async function getJson(path) {
  const response = await fetch(path);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    throw new APIError(
      detail?.code || `HTTP_${response.status}`,
      detail?.message || "无法读取 DeepSeek Trace。",
      response.status,
      detail || {},
    );
  }
  return payload;
}

async function getText(path) {
  const response = await fetch(path);
  const payload = await response.text();
  if (!response.ok) throw new APIError(`HTTP_${response.status}`, "无法读取易读日志。", response.status);
  return payload;
}

function setRunning(running) {
  state.running = running;
  elements.runButton.disabled = running;
  elements.runButton.innerHTML = running
    ? '<span class="spinner" aria-hidden="true"></span>正在理解数据…'
    : '开始翻译 <span>→</span>';
}

function updateRail(activeIndex) {
  elements.railSteps.forEach((step, index) => {
    step.classList.toggle("is-active", index === activeIndex);
    step.classList.toggle("is-complete", index < activeIndex);
  });
}

function renderLoading() {
  updateRail(1);
  elements.resultRoot.className = "result-shell";
  elements.resultRoot.innerHTML = `
    <div class="loading-state">
      <span class="loading-pulse" aria-hidden="true"></span>
      <div><p class="eyebrow">PIPELINE RUNNING</p><h2>正在建立可追溯语义链路</h2>
      <p id="translation-progress" role="status">正在提交任务。OCR 中间结果生成后即可浏览。</p></div>
    </div>`;
}

function errorStageLabel(code, details) {
  if (details?.stageLabel) return details.stageLabel;
  if (code.startsWith("PDF_")) return "PDF 文件解析";
  if (code.startsWith("SEMANTIC_") || code.startsWith("DEEPSEEK_")) return "语义映射";
  if (code.startsWith("CANONICAL_")) return "Canonical 构建";
  return "输入处理";
}

function renderOcrIntermediate(artifact) {
  let panel = document.querySelector("#ocr-intermediate");
  if (!artifact) {
    panel?.remove();
    return;
  }
  if (!panel) {
    panel = document.createElement("section");
    panel.id = "ocr-intermediate";
    panel.className = "ocr-intermediate";
    elements.resultRoot.before(panel);
  }
  // Polling must not reload a preview the user is already reading.
  const key = `${artifact.artifact_id}:${artifact.status}`;
  if (panel.dataset.artifactKey === key) return;
  panel.dataset.artifactKey = key;
  const available = ["complete", "partial"].includes(artifact.status);
  const partial = artifact.status === "partial";
  panel.innerHTML = `
    <h2>OCR 中间结果${partial ? " · 部分完成" : ""}</h2>
    <p>${escapeHtml(artifact.file_name)} · 已识别 ${artifact.completed_pages.length} / ${artifact.total_pages} 页</p>
    <p class="ocr-local-path">已保存到本地：${escapeHtml(artifact.local_path)}</p>
    ${available ? `<div class="ocr-actions"><button type="button" class="button" data-ocr-original>浏览原始文件</button><a class="button" href="${escapeHtml(artifact.download_url)}">下载 OCR 中间结果</a><a href="${escapeHtml(artifact.raw_url)}" target="_blank" rel="noopener">下载原始 OCR JSON</a></div>
    ${artifact.comparison_url ? `<details class="ocr-preview" data-ocr-comparison><summary>对照浏览源文件与 OCR 中间结果</summary><div data-ocr-comparison-body></div></details><p data-ocr-hint>红框对应 OCR block_bbox；页面外的 R 编号和类型对应原始识别区域。左右两栏同步滚动、缩放和翻页。</p>` : `<details class="ocr-preview"><summary>浏览 OCR 中间结果</summary><iframe title="OCR 原始识别结果 PDF" data-src="${escapeHtml(artifact.preview_url)}"></iframe></details>`}
    ${artifact.source_regions_download_url ? `<a class="button" href="${escapeHtml(artifact.source_regions_download_url)}">下载源文件 OCR 区域红框</a>` : ""}` : `<p>中间 PDF 未能生成。${escapeHtml(artifact.error || "请检查服务日志。")}</p><a href="${escapeHtml(artifact.raw_url)}" target="_blank" rel="noopener">下载已保存的 OCR JSON</a>`}
    <p>内容来自 OCR 输出，未经切片、语义映射或数字纠正。${partial ? `已完成原文页：${escapeHtml(artifact.completed_pages.join("、"))}；其余页面未完成。` : ""}</p>`;
  panel.querySelector("[data-ocr-original]")?.addEventListener("click", () => openLocalFile(state.translationFile));
  panel.querySelectorAll("details").forEach(details => details.addEventListener("toggle", () => {
    const frame = details.querySelector("iframe");
    if (frame && details.open && !frame.getAttribute("src")) frame.src = frame.dataset.src;
    if (details.open && details.hasAttribute("data-ocr-comparison") && !details.dataset.loaded) {
      details.dataset.loaded = "true";
      loadOcrComparison(details, artifact).catch(error => {
        details.dataset.loaded = "";
        details.querySelector("[data-ocr-comparison-body]").textContent = `对照视图加载失败：${error.message}。请收起后重新打开。`;
      });
    }
  }));
}

function drawOcrComparisonPage(figure, page, width, zoom, visible, interactive) {
  const image = figure.querySelector("img");
  const svg = figure.querySelector("svg");
  const tags = figure.querySelector(".ocr-region-tags");
  svg.replaceChildren();
  tags.replaceChildren();
  const regions = interactive ? (page.regions || []).filter(region =>
    Array.isArray(region.bbox) && region.bbox.length === 4 &&
    region.bbox.every(Number.isFinite) && region.bbox[2] > region.bbox[0] &&
    region.bbox[3] > region.bbox[1]) : [];
  const showTags = visible.number || visible.type;
  const routes = [];
  if (showTags) {
    [...regions].sort((a, b) => a.bbox[1] - b.bbox[1]).forEach(region => {
      routes.push({ region, ...OcrComparisonLayout.chooseOcrLeader(
        region, regions, page.width, routes) });
    });
  }
  const railWidth = 136 * zoom;
  const leftRail = routes.some(route => route.side === "left") ? railWidth : 0;
  const rightRail = routes.some(route => route.side === "right") ? railWidth : 0;
  const imageWidth = Math.max(100, width - leftRail - rightRail);
  const scale = imageWidth / page.width;
  const imageHeight = page.height * scale;
  const maxCount = Math.max(routes.filter(route => route.side === "left").length,
    routes.filter(route => route.side === "right").length, 1);
  const tagHeight = Math.max(16 * zoom, Math.min(25 * zoom, imageHeight / maxCount - 3 * zoom));
  const gap = 3 * zoom;
  routes.forEach(route => { route.anchor = route.y * scale; });
  const height = Math.max(OcrComparisonLayout.positionOcrTags(routes, "left", imageHeight, tagHeight, gap),
    OcrComparisonLayout.positionOcrTags(routes, "right", imageHeight, tagHeight, gap));
  OcrComparisonLayout.assignOcrLeaderLanes(routes, "left", tagHeight, zoom);
  OcrComparisonLayout.assignOcrLeaderLanes(routes, "right", tagHeight, zoom);
  const stageWidth = leftRail + imageWidth + rightRail;
  figure.style.width = `${stageWidth}px`;
  figure.style.height = `${height}px`;
  image.style.left = `${leftRail}px`;
  image.style.width = `${imageWidth}px`;
  image.style.height = `${imageHeight}px`;
  svg.setAttribute("viewBox", `0 0 ${stageWidth} ${height}`);
  svg.style.width = `${stageWidth}px`;
  svg.style.height = `${height}px`;
  const svgNs = "http://www.w3.org/2000/svg";
  function addSvg(name, attributes) {
    const element = document.createElementNS(svgNs, name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    svg.append(element);
  }
  if (visible.boundary) {
    regions.forEach(region => {
      const [x0, top, x1, bottom] = region.bbox;
      addSvg("rect", { x: leftRail + x0 * scale, y: top * scale,
        width: (x1 - x0) * scale, height: (bottom - top) * scale,
        class: "ocr-region-outline" });
    });
  }
  routes.forEach(route => {
    const [x0, , x1] = route.region.bbox;
    const left = route.side === "left";
    const tagX = left ? 0 : leftRail + imageWidth + 36 * zoom;
    const tagWidth = railWidth - 36 * zoom;
    const startX = left ? tagX + tagWidth : tagX;
    const laneOffset = (5 + route.lane * 6) * zoom;
    const bendX = left ? leftRail - laneOffset : leftRail + imageWidth + laneOffset;
    const endX = left ? leftRail + x0 * scale : leftRail + x1 * scale;
    addSvg("path", { d: `M ${startX} ${route.tagY + tagHeight / 2} H ${bendX} V ${route.anchor} H ${endX}`,
      class: "ocr-region-leader" });
    const tag = document.createElement("div");
    tag.className = "ocr-region-tag";
    tag.style.left = `${tagX}px`;
    tag.style.top = `${route.tagY}px`;
    tag.style.width = `${tagWidth}px`;
    tag.style.height = `${tagHeight}px`;
    tag.style.fontSize = `${Math.min(13, Math.max(9, tagHeight / zoom - 7)) * zoom}px`;
    tag.title = `${route.region.number} · ${route.region.type}`;
    if (visible.number) {
      const number = document.createElement("span");
      number.textContent = route.region.number;
      tag.append(number);
    }
    if (visible.number && visible.type) {
      const separator = document.createElement("span");
      separator.textContent = " · ";
      tag.append(separator);
    }
    if (visible.type) {
      const type = document.createElement("span");
      type.textContent = route.region.type;
      tag.append(type);
    }
    tags.append(tag);
  });
}

async function loadOcrComparison(details, artifact) {
  const body = details.querySelector("[data-ocr-comparison-body]");
  body.textContent = "正在加载对照视图…";
  const data = await getJson(artifact.comparison_url);
  if (!details.isConnected) return;
  if (!data.interactive) {
    details.parentElement.querySelector("[data-ocr-hint]").textContent =
      "旧产物的红框与标签已经画入 PDF，仍按原样显示；左右两栏同步滚动、缩放和翻页。";
  }
  const sourcePage = (page, index) => data.interactive ? page.source_page : (artifact.completed_pages[index] ?? page.index);
  body.innerHTML = `<div class="ocr-compare-toolbar"><button type="button" data-zoom-out aria-label="缩小两栏">−</button><span data-zoom-label>100%</span><button type="button" data-zoom-in aria-label="放大两栏">+</button><button type="button" data-zoom-reset>适合宽度</button><label>原文页 <select data-compare-page>${data.pages.map((page, i) => `<option value="${i}">${escapeHtml(sourcePage(page, i))}</option>`).join("")}</select></label>${data.interactive ? `<fieldset class="ocr-visibility"><legend>标注</legend><label><input type="checkbox" data-ocr-visible="boundary" checked>显示边界</label><label><input type="checkbox" data-ocr-visible="number" checked>显示编号</label><label><input type="checkbox" data-ocr-visible="type" checked>显示类型</label></fieldset>` : ""}<span>滚动 / Ctrl + 滚轮缩放，两栏同步</span></div><div class="ocr-compare-columns">${["source", "result"].map(kind => `<section><h3>${kind === "source" ? "源文件 · OCR 区域" : "OCR 中间结果 · 识别区域"}</h3><div class="ocr-compare-scroll" tabindex="0" aria-label="${kind === "source" ? "源文件" : "OCR 结果"}同步浏览"><div class="ocr-compare-pages">${data.pages.map((page, i) => `<figure data-page-index="${i}"><img loading="lazy" src="${escapeHtml(page[kind + "_url"])}" width="${page.width}" height="${page.height}" alt="原文第 ${escapeHtml(sourcePage(page, i))} 页${kind === "source" ? "源文件" : "OCR 结果"}"><svg aria-hidden="true"></svg><div class="ocr-region-tags"></div></figure>`).join("")}</div></div></section>`).join("")}</div>`;
  const panes = [...body.querySelectorAll(".ocr-compare-scroll")];
  const pageSelect = body.querySelector("[data-compare-page]");
  let zoom = 1;
  let syncing = false;
  const visible = { boundary: true, number: true, type: true };
  function syncScroll(from, to) {
    if (syncing) return;
    if (Math.abs(to.scrollTop - from.scrollTop) < 1 && Math.abs(to.scrollLeft - from.scrollLeft) < 1) return;
    syncing = true;
    to.scrollTop = from.scrollTop;
    to.scrollLeft = from.scrollLeft;
    syncing = false;
  }
  function setZoom(value) {
    const firstFigure = [...panes[0].querySelectorAll("figure")].findLast(figure =>
      figure.offsetTop <= panes[0].scrollTop + 16) || panes[0].querySelector("figure");
    const pageIndex = Number(firstFigure.dataset.pageIndex);
    const fraction = (panes[0].scrollTop - firstFigure.offsetTop) / Math.max(firstFigure.offsetHeight, 1);
    const horizontal = panes[0].scrollLeft / Math.max(panes[0].scrollWidth, 1);
    zoom = value;
    const width = Math.max(100, Math.min(...panes.map(pane => pane.clientWidth)) - 24) * zoom;
    syncing = true;
    panes.forEach(pane => {
      pane.querySelector(".ocr-compare-pages").style.width = `${width}px`;
      pane.querySelectorAll("figure").forEach((figure, index) =>
        drawOcrComparisonPage(figure, data.pages[index], width, zoom, visible, data.interactive));
      const selected = pane.querySelectorAll("figure")[pageIndex];
      pane.scrollTop = selected.offsetTop + fraction * selected.offsetHeight;
      pane.scrollLeft = horizontal * pane.scrollWidth;
    });
    syncing = false;
    body.querySelector("[data-zoom-label]").textContent = `${Math.round(zoom * 100)}%`;
  }
  body.querySelectorAll("[data-ocr-visible]").forEach(input => input.addEventListener("change", () => {
    const key = input.dataset.ocrVisible;
    Object.assign(visible, OcrComparisonLayout.updateOcrVisibility(visible, key, input.checked));
    body.querySelectorAll("[data-ocr-visible]").forEach(control => {
      control.checked = visible[control.dataset.ocrVisible];
    });
    setZoom(zoom);
  }));
  panes.forEach((pane, i) => {
    pane.addEventListener("scroll", () => {
      syncScroll(pane, panes[1 - i]);
      const figures = [...pane.querySelectorAll("figure")];
      const index = figures.findLastIndex(figure => figure.offsetTop - figures[0].offsetTop <= pane.scrollTop + 16);
      pageSelect.value = String(Math.max(0, index));
    });
    pane.addEventListener("wheel", event => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      setZoom(Math.max(0.5, Math.min(3, zoom + (event.deltaY < 0 ? 0.1 : -0.1))));
    }, { passive: false });
    pane.querySelectorAll("img").forEach(img => img.addEventListener("error", () => {
      const message = document.createElement("span");
      message.className = "ocr-page-error";
      message.textContent = "本页加载失败，请收起后重新打开对照视图。";
      img.replaceWith(message);
      details.dataset.loaded = "";
    }));
  });
  body.querySelector("[data-zoom-out]").addEventListener("click", () => setZoom(Math.max(0.5, zoom - 0.1)));
  body.querySelector("[data-zoom-in]").addEventListener("click", () => setZoom(Math.min(3, zoom + 0.1)));
  body.querySelector("[data-zoom-reset]").addEventListener("click", () => setZoom(1));
  pageSelect.addEventListener("change", () => panes.forEach(pane => {
    const figures = pane.querySelectorAll("figure");
    pane.scrollTop = figures[Number(pageSelect.value)].offsetTop - figures[0].offsetTop;
  }));
  const observer = new ResizeObserver(() => {
    if (!details.isConnected) { observer.disconnect(); return; }
    if (details.open) setZoom(zoom);
  });
  observer.observe(panes[0]);
  setZoom(1);
}

async function waitForTranslation(statusUrl) {
  for (;;) {
    const job = await getJson(statusUrl);
    renderOcrIntermediate(job.ocr_intermediate);
    renderAvailableDeepSeekTrace(job.deepseek_trace);
    const progress = document.querySelector("#translation-progress");
    const stages = { queued: "任务已排队，等待前一个任务完成。", ingest: "正在解析文件并判断是否需要 OCR。", ocr: `正在 OCR 识别，第 ${job.page || 1} / ${job.total_pages || "—"} 页。`, ocr_artifact: "OCR 中间结果已保存，正在整理识别结果。", semantic_map: job.ocr_intermediate ? "OCR 已完成，正在进行语义映射；现在可以浏览 OCR 中间结果。" : "本文件未使用 OCR，正在进行语义映射。" };
    if (progress) progress.textContent = stages[job.stage] || "正在完成转换。";
    if (job.status === "completed") return job.result;
    if (job.status === "failed") {
      const error = { ...(job.error || {}), deepseek_trace: job.deepseek_trace || job.error?.deepseek_trace };
      throw new APIError(error.code || "TRANSLATION_FAILED", error.message || "转换失败。", 422, error);
    }
    await new Promise((resolve) => window.setTimeout(resolve, 750));
  }
}

function renderError(code, message, details = {}) {
  updateRail(0);
  const stageLabel = errorStageLabel(code, details);
  const resolution = details?.resolution
    ? `<div class="error-resolution"><strong>建议处理方式</strong><p>${escapeHtml(details.resolution)}</p></div>`
    : "";
  const context = details?.context && Object.keys(details.context).length
    ? `<details class="error-context"><summary>查看技术详情</summary><pre>${escapeHtml(JSON.stringify(details.context, null, 2))}</pre></details>`
    : "";
  elements.resultRoot.className = "result-shell";
  elements.resultRoot.innerHTML = `
    <div class="error-state" role="alert">
      <span class="error-mark">!</span>
      <div><p class="eyebrow">${escapeHtml(code)}</p><h2>本次转换未完成</h2>
      <p class="error-stage"><strong>停止阶段：</strong>${escapeHtml(stageLabel)}</p>
      <p>${escapeHtml(message)}</p>${resolution}${context}</div>
    </div>${renderDeepSeekTraceShell(details.deepseekTrace || details.deepseek_trace)}`;
  wireResultActions(details.deepseekTrace || details.deepseek_trace);
}

function reviewState(mapping) {
  if (mapping.status === "UNMAPPED") return ["UNMAPPED", "danger"];
  if (mapping.status === "AMBIGUOUS") return ["AMBIGUOUS", "danger"];
  if (mapping.confidence >= 0.95) return ["AUTO_ACCEPTED", "success"];
  if (mapping.confidence >= 0.80) return ["ACCEPTED_WITH_WARNING", "warning"];
  return ["REVIEW_REQUIRED", "danger"];
}

function renderMapping(mapping, index) {
  const [label, tone] = reviewState(mapping);
  const confidence = Math.round((mapping.confidence || 0) * 100);
  const source = mapping.source_field || mapping.source_text || mapping.provenance?.raw_text || mapping.source_block_id;
  const destination = mapping.status === "MAPPED"
    ? `<strong>${escapeHtml(mapping.ontology_concept)}</strong><code>${escapeHtml(mapping.canonical_path)}</code>`
    : mapping.status === "AMBIGUOUS"
      ? `<strong>候选 Concept</strong><code>${escapeHtml(mapping.candidate_concepts.join(" · "))}</code>`
      : `<strong>未找到可验证 Concept</strong><code>DO NOT GUESS</code>`;
  const value = mapping.status === "MAPPED"
    ? `${escapeHtml(typeof mapping.value === "object" ? JSON.stringify(mapping.value) : mapping.value)} ${escapeHtml(mapping.canonical_unit || mapping.source_unit || "")}`
    : escapeHtml(mapping.source_text || "—");
  return `
    <article class="mapping-row" data-mapping-index="${index}">
      <div class="mapping-source"><span class="mapping-kicker">原始证据 · ${escapeHtml(mapping.source_block_id)}</span><strong>${escapeHtml(source)}</strong><small>${escapeHtml(mapping.provenance?.source_location || "位置未提供")}</small></div>
      <span class="mapping-arrow" aria-hidden="true">→</span>
      <div class="mapping-target">${destination}<small>${value}</small></div>
      <div class="confidence-cell">
        <span class="status-pill status-${tone}">${label}</span>
        <strong>${confidence}%</strong>
        <span class="confidence-track"><i style="width:${confidence}%"></i></span>
      </div>
    </article>`;
}

function renderReview(mappings, status) {
  const unresolved = mappings.filter((mapping) => mapping.status !== "MAPPED");
  const lowConfidence = mappings
    .map((mapping, index) => ({ mapping, index }))
    .filter(({ mapping }) => mapping.status === "MAPPED" && mapping.confidence < 0.80);
  const warnings = mappings.filter((mapping) => mapping.status === "MAPPED" && mapping.confidence >= 0.80 && mapping.confidence < 0.95);

  if (status !== "review_required") {
    return `<div class="review-pass"><span>✓</span><div><strong>语义安全门已通过</strong><p>${warnings.length} 项带警告接受，无低于 80% 的映射或未解决项。</p></div></div>`;
  }

  const unresolvedMarkup = unresolved.map((mapping) => `
    <div class="review-item review-blocked">
      <span>${mapping.status}</span>
      <div><strong>${escapeHtml(mapping.source_field || mapping.source_text || mapping.source_block_id)}</strong>
      <p>${mapping.status === "AMBIGUOUS" ? `候选：${escapeHtml(mapping.candidate_concepts.join("、"))}` : "Ontology 中没有可验证的映射，系统不会猜测。"}</p></div>
    </div>`).join("");
  const reviewMarkup = lowConfidence.map(({ mapping, index }) => `
    <label class="review-item review-check">
      <input type="checkbox" data-review-index="${index}" />
      <span class="check-box" aria-hidden="true"></span>
      <div><strong>${escapeHtml(mapping.source_text || mapping.ontology_concept)} → ${escapeHtml(mapping.ontology_concept)}</strong>
      <p>置信度 ${Math.round(mapping.confidence * 100)}%，请核对原文、Concept 和 Canonical 路径。</p></div>
    </label>`).join("");
  const canApprove = unresolved.length === 0 && lowConfidence.length > 0;
  return `
    <div class="review-banner"><span>!</span><div><strong>需要人工审查</strong><p>当前流水线已停在 Semantic 之后，尚未构建 Canonical 或生成目标文件。</p></div></div>
    <div class="review-list">${unresolvedMarkup}${reviewMarkup}</div>
    ${canApprove ? `<button class="button button-primary review-continue" id="review-continue" disabled>确认所有低置信度映射并继续 <span>→</span></button><p class="session-note">本次审批仅在当前浏览器会话中有效，不会改写 Ontology 或 Source Mapping。</p>` : ""}
    ${unresolved.length ? '<p class="session-note">未映射或歧义项不能在界面中强行放行；请补充来源上下文或受控映射后重试。</p>' : ""}`;
}

function renderBlocks(source) {
  return (source?.blocks || []).map((block) => {
    const content = typeof block.content === "string" ? block.content : JSON.stringify(block.content, null, 2);
    return `<details class="source-block" ${source.blocks.length === 1 ? "open" : ""}>
      <summary><span>${escapeHtml(block.block_id)}</span><strong>${escapeHtml(block.block_type)}</strong><small>${escapeHtml(block.source_location || "位置未提供")}</small></summary>
      <pre>${escapeHtml(content)}</pre>
    </details>`;
  }).join("");
}

function renderValidationCard(title, validation) {
  if (!validation) return `<div class="validation-card is-empty"><span>${escapeHtml(title)}</span><strong>未执行</strong></div>`;
  const issues = [...validation.errors, ...validation.warnings];
  return `<div class="validation-card ${validation.valid ? "is-valid" : "is-invalid"}">
    <div class="validation-title"><span>${escapeHtml(title)}</span><strong>${validation.valid ? "PASS" : "FAILED"}</strong></div>
    <div class="validation-counts"><b>${validation.errors.length}</b> errors <b>${validation.warnings.length}</b> warnings</div>
    ${issues.length ? `<ul>${issues.map((issue) => `<li class="issue-${validation.errors.includes(issue) ? "error" : "warning"}"><code>${escapeHtml(issue.code)}</code><strong>${escapeHtml(issue.path)}</strong><span>${escapeHtml(issue.message)}</span></li>`).join("")}</ul>` : '<p class="no-issues">未发现阻断性问题。</p>'}
  </div>`;
}

function renderTrace(trace) {
  return (trace || []).map((event, index) => `
    <li class="trace-${event.status}"><span>${String(index + 1).padStart(2, "0")}</span><div><strong>${escapeHtml(event.stage.replaceAll("_", " "))}</strong>${event.detail ? `<small>${escapeHtml(event.detail)}</small>` : ""}</div><b>${escapeHtml(event.status)}</b></li>`).join("");
}

function renderAvailableDeepSeekTrace(summary) {
  if (!summary) return;
  let panel = document.querySelector("#available-deepseek-trace");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "available-deepseek-trace";
    elements.resultRoot.appendChild(panel);
  }
  if (panel.dataset.traceUrl === summary.trace_url) return;
  panel.dataset.traceUrl = summary.trace_url;
  panel.innerHTML = renderDeepSeekTraceShell(summary);
  wireResultActions(summary);
}

function renderDeepSeekTraceShell(summary) {
  if (!summary) return "";
  return `<section class="deepseek-trace-section">
    <div class="deepseek-trace-heading">
      <div><p class="step-label">DEEPSEEK API TRACE</p><h2>模型调用与 Token 明细 <small class="trace-ui-version">BLOCK DIAGNOSTICS V2</small></h2></div>
      <button class="button button-secondary trace-toggle" type="button" data-toggle="deepseek-trace">显示调用明细</button>
    </div>
    <div class="trace-summary-grid">
      <span><b>${summary.api_requests}</b> API 请求</span>
      <span><b>${summary.retry_requests}</b> 重试</span>
      <span><b>${summary.local_corrections ?? 0}</b> 本地更正通过</span>
      <span><b>${summary.avoided_network_retries ?? 0}</b> 避免网络重试</span>
      <span><b>${summary.input_tokens}</b> 输入 Tokens</span>
      <span><b>${summary.output_tokens}</b> 输出 Tokens</span>
      <span><b>${summary.total_tokens}</b> 总 Tokens</span>
    </div>
    <div id="deepseek-trace-detail" hidden></div>
  </section>`;
}

function responseOutputText(responsePayload) {
  if (!responsePayload || typeof responsePayload !== "object") return "";
  const parts = [];
  for (const item of responsePayload.output || []) {
    for (const content of item?.content || []) {
      if (content?.type === "output_text" && typeof content.text === "string") parts.push(content.text);
    }
  }
  return parts.join("\n");
}

function parsedResponseOutput(call) {
  const outputText = responseOutputText(call.response_payload);
  if (!outputText) return { outputText: "", value: null };
  try {
    return { outputText, value: JSON.parse(outputText) };
  } catch {
    return { outputText, value: null };
  }
}

function parsedPrompt(call) {
  const prompt = call.request_payload?.input || "";
  const marker = "\nINPUT:\n";
  const correctionMarker = "\nCORRECTION REQUIRED:\n";
  const markerIndex = prompt.indexOf(marker);
  if (markerIndex < 0) return { system: prompt, input: null, correction: null };
  const system = prompt.slice(0, markerIndex);
  const remainder = prompt.slice(markerIndex + marker.length);
  const correctionIndex = remainder.indexOf(correctionMarker);
  const inputText = correctionIndex < 0 ? remainder : remainder.slice(0, correctionIndex);
  const correctionText = correctionIndex < 0 ? "" : remainder.slice(correctionIndex + correctionMarker.length);
  try {
    return {
      system,
      input: JSON.parse(inputText),
      correction: correctionText ? JSON.parse(correctionText) : null,
    };
  } catch {
    return { system, input: null, correction: correctionText || null };
  }
}

function formatPromptInputForDisplay(prompt) {
  if (typeof prompt !== "string") return JSON.stringify(prompt, null, 2);
  const marker = "\nINPUT:\n";
  const correctionMarker = "\nCORRECTION REQUIRED:\n";
  const markerIndex = prompt.indexOf(marker);
  if (markerIndex < 0) return prompt;

  const system = prompt.slice(0, markerIndex);
  const remainder = prompt.slice(markerIndex + marker.length);
  const correctionIndex = remainder.indexOf(correctionMarker);
  const inputText = correctionIndex < 0 ? remainder : remainder.slice(0, correctionIndex);
  const correctionText = correctionIndex < 0 ? "" : remainder.slice(correctionIndex + correctionMarker.length);
  const prettyJson = (value) => {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  };

  const formattedInput = `${system}${marker}${prettyJson(inputText)}`;
  return correctionIndex < 0
    ? formattedInput
    : `${formattedInput}${correctionMarker}${prettyJson(correctionText)}`;
}

function groupDeepSeekCalls(calls) {
  const groups = new Map();
  calls.forEach((call, index) => {
    const blockId = call.source_block_id || "未识别 Block";
    if (!groups.has(blockId)) groups.set(blockId, []);
    groups.get(blockId).push({ ...call, trace_index: index });
  });
  return [...groups.entries()].map(([blockId, blockCalls]) => ({ blockId, calls: blockCalls }));
}

function groupTraceCallChains(calls) {
  const chains = new Map();
  let inferredChain = null;
  calls.forEach((call, index) => {
    if (call.attempt_group_id) inferredChain = call.attempt_group_id;
    else if (call.call_reason === "initial" || inferredChain === null) inferredChain = `legacy-${index}`;
    const chainId = call.attempt_group_id || inferredChain;
    if (!chains.has(chainId)) chains.set(chainId, []);
    chains.get(chainId).push(call);
  });
  return [...chains.values()];
}

function traceOutcomeTone(call) {
  if (call.error_code || ["output_invalid", "contract_invalid", "http_error", "network_error"].includes(call.outcome)) return "error";
  if (call.local_correction) return "warning";
  return "success";
}

function traceOutcomeLabel(call) {
  const labels = {
    accepted: "通过",
    accepted_after_local_correction: "本地更正后通过",
    output_invalid: "输出无效",
    contract_invalid: "业务契约无效",
    http_error: "HTTP 错误",
    network_error: "网络错误",
    accepted_pending_validation: "等待验证",
  };
  return labels[call.outcome] || call.outcome || call.status || "未知";
}

function renderTraceError(call) {
  if (!call.error_code && !call.error_message) return "";
  const fields = (call.error_details || []).map((detail) => {
    const location = Array.isArray(detail.location) ? detail.location.join(".") : detail.location || detail.path;
    const input = detail.input === undefined ? "" : `<small>错误值：${escapeHtml(typeof detail.input === "object" ? JSON.stringify(detail.input) : detail.input)}</small>`;
    return `<li><button type="button" data-trace-error-path="${escapeHtml(location || "<root>")}">${escapeHtml(location || "<root>")}</button><span>${escapeHtml(detail.message || detail.type)}<code>${escapeHtml(detail.type || "")}</code>${input}</span></li>`;
  }).join("");
  return `<div class="trace-error-panel" role="alert">
    <div><span>异常</span><strong>${escapeHtml(call.error_code || "API_CALL_FAILED")}</strong></div>
    <p>${escapeHtml(call.error_message || "本次调用未通过验证。")}</p>
    ${fields ? `<ul>${fields}</ul>` : ""}
  </div>`;
}

function traceArrayIdentity(item) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const field = ["canonical_path", "concept_id", "block_id", "id"]
    .find((candidate) => typeof item[candidate] === "string" && item[candidate]);
  return field ? `${field}=${item[field]}` : null;
}

function collectTraceDiffs(before, after, path = "$", changes = []) {
  if (before === undefined || after === undefined) {
    changes.push({ path, before, after });
    return changes;
  }
  if (before === null || after === null || typeof before !== "object" || typeof after !== "object") {
    if (JSON.stringify(before) !== JSON.stringify(after)) changes.push({ path, before, after });
    return changes;
  }
  if (Array.isArray(before) && Array.isArray(after)) {
    const beforeIds = before.map(traceArrayIdentity);
    const afterIds = after.map(traceArrayIdentity);
    const identitiesAreStable = [...beforeIds, ...afterIds].every(Boolean)
      && new Set(beforeIds).size === beforeIds.length
      && new Set(afterIds).size === afterIds.length;
    if (identitiesAreStable) {
      const beforeMap = new Map(beforeIds.map((identity, index) => [identity, before[index]]));
      const afterMap = new Map(afterIds.map((identity, index) => [identity, after[index]]));
      [...new Set([...beforeIds, ...afterIds])].forEach((identity) => {
        collectTraceDiffs(beforeMap.get(identity), afterMap.get(identity), `${path}[${identity}]`, changes);
      });
    } else {
      const length = Math.max(before.length, after.length);
      for (let index = 0; index < length; index += 1) collectTraceDiffs(before[index], after[index], `${path}[${index}]`, changes);
    }
    return changes;
  }
  if (Array.isArray(before) !== Array.isArray(after)) {
    changes.push({ path, before, after });
    return changes;
  }
  [...new Set([...Object.keys(before), ...Object.keys(after)])].forEach((key) => {
    collectTraceDiffs(before[key], after[key], `${path}.${key}`, changes);
  });
  return changes;
}

function renderTraceDiff(calls) {
  if (calls.length < 2) return "";
  const beforeCall = calls.find((call) => traceOutcomeTone(call) === "error") || calls[0];
  const beforeIndex = calls.indexOf(beforeCall);
  const afterCall = calls.slice(beforeIndex + 1).find((call) => traceOutcomeTone(call) !== "error") || calls.at(-1);
  if (beforeCall === afterCall) return "";
  const beforeOutput = parsedResponseOutput(beforeCall).value;
  const afterOutput = parsedResponseOutput(afterCall).value;
  if (beforeOutput === null || afterOutput === null) {
    return `<section class="trace-diff-panel"><h4>重试结果对比</h4><p>至少一次输出不是可解析的 JSON，无法生成字段级差异；可在下方展开对应尝试查看原始输出。</p></section>`;
  }
  const allChanges = collectTraceDiffs(beforeOutput, afterOutput);
  const supplementaryEvidence = allChanges.filter((change) => change.path.endsWith(".source_text") && change.before === undefined).length;
  const changes = allChanges.filter((change) => !(change.path.endsWith(".source_text") && change.before === undefined)).slice(0, 40);
  const displayValue = (value) => value === undefined ? "∅" : JSON.stringify(value);
  const rows = changes.map((change) => {
    const tone = change.before === undefined ? "added" : change.after === undefined ? "removed" : "changed";
    return `<li class="trace-diff-${tone}"><code>${escapeHtml(change.path)}</code><span>${escapeHtml(displayValue(change.before))}</span><b>→</b><span>${escapeHtml(displayValue(change.after))}</span></li>`;
  }).join("");
  return `<section class="trace-diff-panel"><h4>失败尝试 → 后续结果的字段差异</h4>
    ${supplementaryEvidence ? `<p class="trace-diff-note">另有 ${supplementaryEvidence} 项映射在重试后补充了 source_text，已折叠以突出契约变化。</p>` : ""}
    ${rows ? `<ul>${rows}</ul>${allChanges.length - supplementaryEvidence > 40 ? "<p>仅显示前 40 项主要差异。</p>" : ""}` : "<p>两次调用的结构化输出没有其他字段级差异。</p>"}
  </section>`;
}

function renderPromptSemanticView(call) {
  const prompt = parsedPrompt(call);
  if (!prompt.input) {
    return `<p class="trace-prompt-unparsed">Prompt 不是可分区格式，请在“完整 System Prompt / Input”中查看。</p>`;
  }
  const rawBlock = prompt.input.raw_block || {};
  const rawContent = typeof rawBlock.content === "string"
    ? rawBlock.content
    : JSON.stringify(rawBlock.content, null, 2);
  const candidates = (prompt.input.ontology_candidates || []).map((candidate) => `
    <li><strong>${escapeHtml(candidate.concept_id)}</strong><code>${escapeHtml(candidate.canonical_path_template)}</code><small>${escapeHtml(candidate.description || "")}</small></li>`).join("");
  return `<div class="trace-semantic-grid">
    <section><h5>映射范围</h5><dl><dt>证据 Block</dt><dd><code>${escapeHtml(prompt.input.mapping_scope?.source_block_id || rawBlock.block_id)}</code></dd><dt>规则</dt><dd>${escapeHtml(prompt.input.mapping_scope?.instruction || "仅映射当前 raw_block")}</dd></dl></section>
    <section><h5>原始证据</h5><pre>${escapeHtml(rawContent || "无内容")}</pre></section>
    <section class="trace-candidates"><h5>Ontology Candidates · ${(prompt.input.ontology_candidates || []).length}</h5><ul>${candidates || "<li>无候选概念</li>"}</ul></section>
    ${prompt.correction ? `<section class="trace-correction-instruction"><h5>CORRECTION REQUIRED</h5><pre>${escapeHtml(JSON.stringify(prompt.correction, null, 2))}</pre></section>` : ""}
    <details><summary>完整 System Prompt</summary><pre>${escapeHtml(prompt.system || "未提供。")}</pre></details>
    <details><summary>完整 Canonical Schema</summary><pre>${escapeHtml(JSON.stringify(prompt.input.canonical_schema || {}, null, 2))}</pre></details>
  </div>`;
}

function renderTraceCode(value) {
  return String(value || "").split("\n").map((line) => `<span class="trace-json-line">${escapeHtml(line || " ")}</span>`).join("");
}

function renderTraceAttempt(call, attemptNumber, previousCall = null) {
  const tone = traceOutcomeTone(call);
  const parsed = parsedResponseOutput(call);
  const formattedOutput = parsed.value === null ? parsed.outputText : JSON.stringify(parsed.value, null, 2);
  const requestInput = call.request_payload?.input || "";
  const formattedRequestInput = formatPromptInputForDisplay(requestInput);
  const requestInstructions = call.request_payload?.instructions || "";
  const inputState = !previousCall
    ? "初始输入"
    : previousCall.request_payload?.input === requestInput
      ? "输入未变化"
      : call.call_reason === "contract_retry" ? "追加纠正指令" : "输入发生变化";
  const validatedOutput = call.validated_output ? JSON.stringify(call.validated_output, null, 2) : "";
  return `<article class="trace-attempt trace-attempt-${tone}">
    <header class="trace-attempt-header">
      <span class="trace-attempt-number">尝试 ${attemptNumber}</span>
      <span class="trace-reason">${escapeHtml(call.call_reason)}</span>
      <strong class="trace-status trace-status-${tone}">${escapeHtml(traceOutcomeLabel(call))}</strong>
      <span class="trace-input-state">${escapeHtml(inputState)}</span>
      <span>${escapeHtml(call.input_tokens ?? "—")} in / ${escapeHtml(call.output_tokens ?? "—")} out</span>
      <span>${Number(call.duration_ms || 0).toFixed(0)} ms</span>
    </header>
    ${renderTraceError(call)}
    ${call.local_correction ? `<p class="trace-correction"><strong>本地更正</strong>${escapeHtml(call.local_correction)}</p>` : ""}
    <div class="trace-attempt-content">
      <section><div class="trace-pane-heading"><h4>API 输出</h4><button type="button" data-trace-copy="output" data-trace-index="${call.trace_index}">复制输出</button></div><pre>${renderTraceCode(formattedOutput || "未返回 output_text。")}</pre></section>
      ${call.local_correction && validatedOutput ? `<section class="trace-normalized-output"><div class="trace-pane-heading"><h4>本地修正后的有效 JSON</h4><button type="button" data-trace-copy="validated" data-trace-index="${call.trace_index}">复制修正结果</button></div><pre>${renderTraceCode(validatedOutput)}</pre></section>` : ""}
      <details class="trace-semantic-prompt"><summary>语义视图：证据、候选与纠正指令</summary>${renderPromptSemanticView(call)}</details>
      <details><summary>完整 Prompt / Input</summary><div class="trace-prompt-sections"><h5>Request Instructions</h5><pre>${escapeHtml(requestInstructions || "未提供。")}</pre><div class="trace-pane-heading trace-prompt-pane-heading"><h5>Input</h5><button type="button" data-trace-download="prompt" data-trace-index="${call.trace_index}">下载格式化 Prompt Log</button></div><pre>${escapeHtml(formattedRequestInput || "未提供。")}</pre></div></details>
      <details><summary>原始 Request / Response</summary><div class="trace-payloads"><div><h4>Request</h4><pre>${escapeHtml(JSON.stringify(call.request_payload, null, 2))}</pre></div><div><h4>Response</h4><pre>${escapeHtml(JSON.stringify(call.response_payload, null, 2))}</pre></div></div></details>
    </div>
  </article>`;
}

function renderTraceBlockGroup(group) {
  const calls = group.calls;
  const finalCall = calls.at(-1);
  const hasProblem = calls.some((call) => traceOutcomeTone(call) === "error");
  const finalTone = traceOutcomeTone(finalCall);
  const totalTokens = calls.reduce((sum, call) => sum + Number(call.total_tokens || 0), 0);
  const duration = calls.reduce((sum, call) => sum + Number(call.duration_ms || 0), 0);
  const retryTokens = calls.slice(1).reduce((sum, call) => sum + Number(call.total_tokens || 0), 0);
  const retryDuration = calls.slice(1).reduce((sum, call) => sum + Number(call.duration_ms || 0), 0);
  const route = calls.map((call) => traceOutcomeLabel(call)).join(" → ");
  const errors = calls.filter((call) => call.error_code);
  const chains = groupTraceCallChains(calls);
  return `<details class="trace-block-group trace-block-${hasProblem ? "recovered" : finalTone}" data-trace-block="${escapeHtml(group.blockId)}" data-has-problem="${hasProblem || calls.length > 1}" ${hasProblem ? "open" : ""}>
    <summary class="trace-block-summary">
      <span><small>BLOCK</small><code>${escapeHtml(group.blockId)}</code></span>
      <span><small>调用过程</small><strong>${escapeHtml(route)}</strong></span>
      <span><small>API 请求</small><strong>${calls.length}</strong></span>
      <span><small>总 Token</small><strong>${totalTokens}</strong></span>
      <span><small>总耗时</small><strong>${duration.toFixed(0)} ms</strong></span>
      <span class="trace-final-status trace-status-${finalTone}"><small>最终状态</small><strong>${escapeHtml(traceOutcomeLabel(finalCall))}</strong></span>
    </summary>
    <div class="trace-block-detail">
      <div class="trace-block-tools"><span>${calls.length > 1 ? `重试额外消耗 ${retryTokens} tokens / ${retryDuration.toFixed(0)} ms` : "本组没有重试成本"}</span><button type="button" data-trace-copy="diagnosis" data-trace-block-id="${escapeHtml(group.blockId)}">复制诊断摘要</button></div>
      ${errors.length ? `<div class="trace-block-diagnosis"><strong>本组发生 ${errors.length} 次异常</strong><span>${errors.map((call) => `${call.error_code}: ${call.error_message || "无异常描述"}`).map(escapeHtml).join("；")}</span></div>` : ""}
      ${chains.map((chain) => renderTraceDiff(chain)).join("")}
      <div class="trace-attempt-list">${calls.map((call, index) => renderTraceAttempt(call, index + 1, calls[index - 1])).join("")}</div>
    </div>
  </details>`;
}

function traceDiagnosisText(group) {
  const calls = group.calls;
  const first = calls[0] || {};
  const finalCall = calls.at(-1) || {};
  const errors = calls.filter((call) => call.error_code);
  const locations = errors.flatMap((call) => call.error_details || []).map((detail) => {
    const location = Array.isArray(detail.location) ? detail.location.join(".") : detail.location || detail.path;
    return `${location || "<root>"} (${detail.type || "validation_error"})`;
  });
  const retryTokens = calls.slice(1).reduce((sum, call) => sum + Number(call.total_tokens || 0), 0);
  const retryDuration = calls.slice(1).reduce((sum, call) => sum + Number(call.duration_ms || 0), 0);
  return [
    `Block: ${group.blockId}`,
    `Initial: ${first.call_reason || "initial"} / ${first.outcome || first.status || "unknown"}`,
    ...errors.map((call) => `Error: ${call.error_code} — ${call.error_message || "无异常描述"}`),
    ...(locations.length ? [`Location: ${locations.join(", ")}`] : []),
    `Final: ${finalCall.call_reason || "unknown"} / ${finalCall.outcome || finalCall.status || "unknown"}`,
    `Retry cost: ${retryTokens} tokens / ${retryDuration.toFixed(0)} ms`,
  ].join("\n");
}

function renderDeepSeekTraceDetail(trace, readableLog) {
  const calls = trace.calls || [];
  const groups = groupDeepSeekCalls(calls);
  return `<div class="trace-file-meta">文件：${escapeHtml(trace.source?.file_name)} · 状态：${escapeHtml(trace.semantic_status)} · ${escapeHtml(trace.created_at)}</div>
    <div class="trace-filters" role="group" aria-label="Trace 过滤"><button class="is-active" type="button" data-trace-filter="all">全部 Block</button><button type="button" data-trace-filter="problems">只看错误 / 重试</button></div>
    <div class="trace-block-list">${groups.map(renderTraceBlockGroup).join("") || '<p class="trace-loading">Trace 中没有 API 调用记录。</p>'}</div>
    <details class="readable-trace-log"><summary>查看去除转义与特殊字符的易读日志</summary><pre>${escapeHtml(readableLog || "易读日志未生成。")}</pre></details>`;
}

function emptyStage(message) {
  return `<div class="stage-empty"><span>—</span><p>${escapeHtml(message)}</p></div>`;
}

function renderResult(result) {
  state.result = result;
  const mappings = result.semantic_mapping?.mappings || [];
  const reviewRequired = result.status === "review_required";
  const railIndex = reviewRequired ? 2 : result.status === "success" ? 4 : 3;
  updateRail(railIndex);
  const mappedCount = mappings.filter((mapping) => mapping.status === "MAPPED").length;
  const unresolvedCount = mappings.length - mappedCount;
  const statusCopy = {
    success: ["转换完成", "success"],
    review_required: ["等待人工审查", "warning"],
    validation_failed: ["Canonical 校验未通过", "danger"],
    export_failed: ["目标平台导出被阻断", "danger"],
  }[result.status] || [result.status, "neutral"];
  const canonicalJson = result.canonical_model ? JSON.stringify(result.canonical_model, null, 2) : "";
  const targetContent = result.target?.content || "";

  elements.resultRoot.className = "result-shell";
  elements.resultRoot.innerHTML = `
    <header class="result-header">
      <div><p class="eyebrow">TRANSLATION ${escapeHtml(result.translation_id?.slice(0, 8) || "REVIEWED")}</p><h2>${statusCopy[0]}</h2></div>
      <div class="result-metrics"><span><b>${mappings.length}</b> 映射项</span><span><b>${unresolvedCount}</b> 未解决</span><span class="status-pill status-${statusCopy[1]}">${escapeHtml(result.status)}</span></div>
    </header>

    <section class="stage-section" id="source-result">
      <div class="stage-heading"><span class="stage-number">01</span><div><p class="step-label">SOURCE</p><h2>解析后的源资料</h2></div><span class="stage-meta">${escapeHtml(result.source?.file_name)} · ${escapeHtml(result.source?.source_type?.toUpperCase())} · ${result.source?.blocks?.length || 0} blocks</span></div>
      <div class="source-blocks">${renderBlocks(result.source)}</div>
    </section>

    <section class="stage-section" id="semantic-result">
      <div class="stage-heading"><span class="stage-number">02</span><div><p class="step-label">SEMANTIC MAPPING</p><h2>证据到 Ontology 的对齐</h2></div><span class="stage-meta">${mappedCount} mapped · ${unresolvedCount} unresolved</span></div>
      <div class="mapping-list">${mappings.map(renderMapping).join("") || emptyStage("模型未返回任何语义映射。")}</div>
    </section>

    <section class="stage-section review-section" id="review-result">
      <div class="stage-heading"><span class="stage-number">03</span><div><p class="step-label">HUMAN REVIEW</p><h2>语义安全门</h2></div><span class="stage-meta">阈值 80% / 95%</span></div>
      ${renderReview(mappings, result.status)}
    </section>

    <section class="stage-section" id="canonical-result">
      <div class="stage-heading"><span class="stage-number">04</span><div><p class="step-label">CANONICAL + VALIDATE</p><h2>平台无关数据模型</h2></div>${canonicalJson ? '<div class="section-actions"><button class="text-button" data-copy="canonical">复制 JSON</button><button class="text-button" data-download="canonical">下载</button></div>' : ""}</div>
      ${canonicalJson ? `<pre class="code-window json-code" id="canonical-code">${escapeHtml(canonicalJson)}</pre>` : emptyStage("安全门通过后才会构建 Canonical Model。")}
      <div class="validation-grid">
        ${renderValidationCard("L1–L3 Canonical Validation", result.validation)}
        ${renderValidationCard("L4 Export Validation", result.export_validation)}
      </div>
    </section>

    <section class="stage-section target-section" id="target-result">
      <div class="stage-heading"><span class="stage-number">05</span><div><p class="step-label">TARGET PLATFORM</p><h2>${escapeHtml(result.target?.platform?.toUpperCase() || selectedTarget().toUpperCase())} 生成结果</h2></div>${targetContent ? '<div class="section-actions"><button class="text-button" data-copy="target">复制文本</button><button class="text-button" data-download="target">下载文件</button></div>' : ""}</div>
      ${targetContent ? `<pre class="code-window target-code" id="target-code">${escapeHtml(targetContent)}</pre>` : emptyStage("导出校验通过后才会渲染目标格式；系统不会跳过上游问题。")}
      ${targetContent ? '<p class="target-disclaimer">当前为 PoC 平台片段。Eclipse 井控输出仍需要宿主 Deck 的 WELSPECS / COMPDAT 上下文；CMG 为未验证版本的 IMEX-style 片段。</p>' : ""}
    </section>

    ${renderDeepSeekTraceShell(result.deepseek_trace)}
    <section class="trace-section"><p class="step-label">EXECUTION TRACE</p><ol>${renderTrace(result.trace)}</ol></section>`;
  wireResultActions();
}

async function continueAfterReview() {
  if (!state.result || state.running) return;
  const mapped = state.result.semantic_mapping.mappings.filter((mapping) => mapping.status === "MAPPED");
  const targetPlatform = selectedTarget();
  setRunning(true);
  const continueButton = document.querySelector("#review-continue");
  if (continueButton) continueButton.textContent = "正在执行确定性构建…";
  try {
    const canonical = await postJson("/canonical/build", { mappings: mapped, schema_version: "0.1.0" });
    const validation = await postJson("/validate", { canonical_model: canonical });
    let exportResult = null;
    if (validation.valid) {
      exportResult = await postJson(`/export/${encodeURIComponent(targetPlatform)}`, { canonical_model: canonical });
    }
    const target = exportResult?.target || null;
    const status = !validation.valid ? "validation_failed" : target ? "success" : "export_failed";
    renderResult({
      ...state.result,
      status,
      canonical_model: canonical,
      validation,
      export_validation: exportResult?.export_validation || null,
      target,
      trace: [
        ...state.result.trace,
        { stage: "human_review", status: "success", detail: "Low-confidence mappings approved in this browser session." },
        { stage: "canonical_build", status: "success" },
        { stage: "validation", status: validation.valid ? "success" : "failed" },
        ...(validation.valid ? [{ stage: "export_validation", status: target ? "success" : "failed" }] : []),
        ...(target ? [{ stage: "render", status: "success" }] : []),
      ],
    });
    document.querySelector("#canonical-result")?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    renderError(error.code || "REVIEW_CONTINUATION_FAILED", error.message || "人工审查后续流程执行失败。", error);
  } finally {
    setRunning(false);
  }
}

function downloadText(content, fileName, type = "text/plain;charset=utf-8") {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([content], { type }));
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 0);
}

function promptLogText(trace, call) {
  const request = call.request_payload || {};
  const requestInstructions = request.instructions || "未提供。";
  const formattedInput = formatPromptInputForDisplay(request.input || "") || "未提供。";
  return [
    "DeepSeek Prompt / Input",
    `Translation: ${trace.translation_id || "unknown"}`,
    `Block: ${call.source_block_id || "unknown"}`,
    `Call: ${Number(call.trace_index) + 1}`,
    `Reason: ${call.call_reason || "unknown"}`,
    `Request ID: ${call.request_id || "unknown"}`,
    "",
    "=== REQUEST INSTRUCTIONS ===",
    requestInstructions,
    "",
    "=== INPUT ===",
    formattedInput,
    "",
  ].join("\n");
}

function safeLogFilePart(value) {
  return String(value || "unknown").replace(/[^a-zA-Z0-9._-]+/g, "-");
}

async function copyWithFeedback(button, content) {
  await navigator.clipboard.writeText(content);
  const original = button.textContent;
  button.textContent = "已复制";
  setTimeout(() => { button.textContent = original; }, 1400);
}

function wireDeepSeekTraceDetail(detail, trace) {
  detail.querySelectorAll('[data-trace-download="prompt"]').forEach((downloadButton) => {
    downloadButton.addEventListener("click", (downloadEvent) => {
      const call = trace.calls?.[Number(downloadEvent.currentTarget.dataset.traceIndex)];
      if (!call) return;
      const fileName = [
        "prompt",
        safeLogFilePart(trace.translation_id),
        safeLogFilePart(call.source_block_id),
        `call-${Number(call.trace_index) + 1}`,
      ].join("-") + ".log";
      downloadText(promptLogText(trace, call), fileName);
    });
  });
  detail.querySelectorAll("[data-trace-copy]").forEach((copyButton) => {
    copyButton.addEventListener("click", (copyEvent) => {
      const action = copyEvent.currentTarget.dataset.traceCopy;
      const blockId = copyEvent.currentTarget.dataset.traceBlockId;
      const call = trace.calls?.[Number(copyEvent.currentTarget.dataset.traceIndex)];
      const group = groupDeepSeekCalls(trace.calls || []).find((item) => item.blockId === blockId);
      if (action === "diagnosis" && !group) return;
      if (action !== "diagnosis" && !call) return;
      const content = action === "diagnosis"
        ? traceDiagnosisText(group)
        : action === "validated"
          ? JSON.stringify(call.validated_output, null, 2)
          : responseOutputText(call.response_payload);
      copyWithFeedback(copyEvent.currentTarget, content);
    });
  });
  detail.querySelectorAll("[data-trace-filter]").forEach((filterButton) => {
    filterButton.addEventListener("click", (filterEvent) => {
      const filter = filterEvent.currentTarget.dataset.traceFilter;
      detail.querySelectorAll("[data-trace-filter]").forEach((item) => item.classList.toggle("is-active", item === filterEvent.currentTarget));
      detail.querySelectorAll(".trace-block-group").forEach((groupElement) => {
        groupElement.hidden = filter === "problems" && groupElement.dataset.hasProblem !== "true";
      });
    });
  });
  detail.querySelectorAll("[data-trace-error-path]").forEach((pathButton) => {
    pathButton.addEventListener("click", (pathEvent) => {
      const attempt = pathEvent.currentTarget.closest(".trace-attempt");
      const output = attempt?.querySelector(".trace-attempt-content > section pre");
      if (!output) return;
      const path = pathEvent.currentTarget.dataset.traceErrorPath || "";
      const parts = path.split(/[.\[\]]/).filter((part) => part && !/^\d+$/.test(part) && !["MAPPED", "UNMAPPED", "AMBIGUOUS"].includes(part));
      const field = parts.at(-1);
      const lines = [...output.querySelectorAll(".trace-json-line")];
      const mappingIndex = path.match(/(?:^|\.)mappings\.(\d+)(?:\.|$)/)?.[1];
      const mappingStarts = lines
        .map((line, index) => line.textContent === "    {" ? index : -1)
        .filter((index) => index >= 0);
      const rangeStart = mappingIndex === undefined ? 0 : mappingStarts[Number(mappingIndex)] ?? 0;
      const rangeEnd = mappingIndex === undefined ? lines.length : mappingStarts[Number(mappingIndex) + 1] ?? lines.length;
      const targetLine = lines.slice(rangeStart, rangeEnd).find((line) => line.textContent.includes(`"${field}"`))
        || lines.find((line) => line.textContent.includes(`"${field}"`))
        || lines[0];
      output.querySelectorAll(".trace-output-focus").forEach((line) => line.classList.remove("trace-output-focus"));
      targetLine?.scrollIntoView({ behavior: "smooth", block: "center" });
      requestAnimationFrame(() => targetLine?.classList.add("trace-output-focus"));
    });
  });
}

function wireResultActions(traceSummary = state.result?.deepseek_trace) {
  const reviewChecks = [...document.querySelectorAll("[data-review-index]")];
  const reviewButton = document.querySelector("#review-continue");
  reviewChecks.forEach((checkbox) => checkbox.addEventListener("change", () => {
    if (reviewButton) reviewButton.disabled = !reviewChecks.every((item) => item.checked);
  }));
  reviewButton?.addEventListener("click", continueAfterReview);

  document.querySelector('[data-toggle="deepseek-trace"]')?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const detail = document.querySelector("#deepseek-trace-detail");
    if (!detail) return;
    if (!detail.hidden) {
      detail.hidden = true;
      button.textContent = "显示调用明细";
      return;
    }
    detail.hidden = false;
    button.textContent = "隐藏调用明细";
    if (detail.dataset.loaded === "true") return;
    detail.innerHTML = '<p class="trace-loading">正在读取本地 Trace 文件…</p>';
    try {
      const [trace, readableLog] = await Promise.all([
        getJson(traceSummary.trace_url),
        getText(traceSummary.readable_log_url),
      ]);
      detail.innerHTML = renderDeepSeekTraceDetail(trace, readableLog);
      detail.dataset.loaded = "true";
      wireDeepSeekTraceDetail(detail, trace);
    } catch (error) {
      detail.innerHTML = `<p class="trace-load-error">${escapeHtml(error.message || "Trace 读取失败。")}</p>`;
    }
  });

  document.querySelector('[data-copy="canonical"]')?.addEventListener("click", (event) => {
    copyWithFeedback(event.currentTarget, JSON.stringify(state.result.canonical_model, null, 2));
  });
  document.querySelector('[data-copy="target"]')?.addEventListener("click", (event) => {
    copyWithFeedback(event.currentTarget, state.result.target.content);
  });
  document.querySelector('[data-download="canonical"]')?.addEventListener("click", () => {
    downloadText(JSON.stringify(state.result.canonical_model, null, 2), `canonical-${state.result.translation_id}.json`, "application/json;charset=utf-8");
  });
  document.querySelector('[data-download="target"]')?.addEventListener("click", () => {
    const extension = state.result.target.platform.toLowerCase() === "eclipse" ? "inc" : "dat";
    downloadText(state.result.target.content, `${state.result.target.platform}-${state.result.translation_id}.${extension}`);
  });
}

async function runTranslation() {
  if (state.running) return;
  const pasted = elements.sourceInput.value.trim();
  if (!state.file && !pasted) {
    renderError("SOURCE_REQUIRED", "请选择一个文件，或在文本框中粘贴原始资料。");
    elements.sourceInput.focus();
    return;
  }
  setRunning(true);
  state.translationFile = state.file;
  renderOcrIntermediate(null);
  renderLoading();
  try {
    const source = state.translationFile ? await fileToSource(state.translationFile) : {
      file_name: "source.txt",
      content: pasted,
      content_encoding: "utf-8",
      source_id: "pasted-source",
    };
    const body = {
      source,
      target_platform: selectedTarget(),
      schema_version: "0.1.0",
    };
    if (elements.sourceSystem.value) body.source_system = elements.sourceSystem.value;
    const job = await postJson("/translation-jobs", body);
    const result = await waitForTranslation(job.status_url);
    renderOcrIntermediate(result.ocr_intermediate);
    renderResult(result);
    elements.resultRoot.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    renderError(error.code || "TRANSLATION_FAILED", error.message || "转换过程中发生未知错误。", error);
  } finally {
    setRunning(false);
  }
}

elements.fileInput.addEventListener("change", (event) => setSelectedFile(event.target.files[0] || null));
elements.fileSummary.addEventListener("click", (event) => {
  if (event.target.closest("#clear-file")) return;
  openSelectedFile();
});
elements.fileSummary.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  openSelectedFile();
});
elements.clearFile.addEventListener("click", (event) => {
  event.stopPropagation();
  setSelectedFile(null);
});
elements.runButton.addEventListener("click", runTranslation);
elements.sourceInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") runTranslation();
});
["dragenter", "dragover"].forEach((name) => elements.dropzone.addEventListener(name, (event) => {
  event.preventDefault();
  elements.dropzone.classList.add("is-dragover");
}));
["dragleave", "drop"].forEach((name) => elements.dropzone.addEventListener(name, (event) => {
  event.preventDefault();
  elements.dropzone.classList.remove("is-dragover");
}));
elements.dropzone.addEventListener("drop", (event) => setSelectedFile(event.dataTransfer.files[0] || null));

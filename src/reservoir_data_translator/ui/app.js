"use strict";

const MAX_SOURCE_BYTES = 16 * 1024 * 1024;
const TEXT_EXTENSIONS = new Set(["txt", "json", "csv"]);
const state = {
  file: null,
  result: null,
  running: false,
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
  constructor(code, message, status) {
    super(message);
    this.name = "APIError";
    this.code = code;
    this.status = status;
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
  if (![...TEXT_EXTENSIONS, "xlsx"].includes(extensionFor(file.name))) {
    renderError("UNSUPPORTED_FILE", "当前仅支持 TXT、JSON、CSV 和 XLSX。");
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
  if (!state.file) return;
  const extension = extensionFor(state.file.name);
  const previewTypes = {
    txt: "text/plain;charset=utf-8",
    json: "application/json;charset=utf-8",
    csv: "text/csv;charset=utf-8",
  };
  const previewFile = previewTypes[extension]
    ? new Blob([state.file], { type: previewTypes[extension] })
    : state.file;
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
    );
  }
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
      <p>解析原文、检索 Ontology 并生成结构化映射。这一步可能需要几秒。</p></div>
    </div>`;
}

function renderError(code, message) {
  updateRail(0);
  elements.resultRoot.className = "result-shell";
  elements.resultRoot.innerHTML = `
    <div class="error-state" role="alert">
      <span class="error-mark">!</span>
      <div><p class="eyebrow">${escapeHtml(code)}</p><h2>本次转换未启动</h2>
      <p>${escapeHtml(message)}</p></div>
    </div>`;
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
    renderError(error.code || "REVIEW_CONTINUATION_FAILED", error.message || "人工审查后续流程执行失败。");
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

async function copyWithFeedback(button, content) {
  await navigator.clipboard.writeText(content);
  const original = button.textContent;
  button.textContent = "已复制";
  setTimeout(() => { button.textContent = original; }, 1400);
}

function wireResultActions() {
  const reviewChecks = [...document.querySelectorAll("[data-review-index]")];
  const reviewButton = document.querySelector("#review-continue");
  reviewChecks.forEach((checkbox) => checkbox.addEventListener("change", () => {
    if (reviewButton) reviewButton.disabled = !reviewChecks.every((item) => item.checked);
  }));
  reviewButton?.addEventListener("click", continueAfterReview);

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
  renderLoading();
  try {
    const source = state.file ? await fileToSource(state.file) : {
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
    const result = await postJson("/translate", body);
    renderResult(result);
    elements.resultRoot.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    renderError(error.code || "TRANSLATION_FAILED", error.message || "转换过程中发生未知错误。");
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

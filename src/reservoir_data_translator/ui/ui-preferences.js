"use strict";

const READABILITY_KEY = "reservoir-translator-readable-text";
const readabilityButton = document.querySelector("[data-readability-toggle]");

function setReadableText(enabled) {
  document.documentElement.classList.toggle("readable-text", enabled);
  if (!readabilityButton) return;
  readabilityButton.setAttribute("aria-pressed", String(enabled));
  readabilityButton.lastChild.textContent = enabled ? " 标准文字" : " 放大文字";
  readabilityButton.title = enabled
    ? "恢复标准文字大小"
    : "放大说明、详情和日志文字";
}

setReadableText(localStorage.getItem(READABILITY_KEY) === "true");
readabilityButton?.addEventListener("click", () => {
  const enabled = !document.documentElement.classList.contains("readable-text");
  localStorage.setItem(READABILITY_KEY, String(enabled));
  setReadableText(enabled);
  window.dispatchEvent(new CustomEvent("ui-readability-change", { detail: { enabled } }));
});

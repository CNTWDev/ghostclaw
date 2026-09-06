/** Object-local generative actions for text selected inside assistant replies. */

const ACTIONS = Object.freeze({
  explain: "请解释下面这段内容，重点说明它的含义和依据：",
  improve: "请改写下面这段内容，使它更清晰、准确、易读：",
  shorten: "请在保留关键信息的前提下精简下面这段内容：",
});

export function initSelectionActions({ messages, input } = {}) {
  if (!messages || !input) return () => {};
  const toolbar = document.createElement("div");
  toolbar.className = "ai-selection-actions hidden";
  toolbar.setAttribute("role", "toolbar");
  toolbar.setAttribute("aria-label", "Actions for selected assistant text");
  toolbar.innerHTML = `
    <span class="ai-selection-actions-label">Ask Pip</span>
    <button type="button" data-selection-action="explain">Explain</button>
    <button type="button" data-selection-action="improve">Improve</button>
    <button type="button" data-selection-action="shorten">Shorten</button>`;
  document.body.appendChild(toolbar);

  let selectedText = "";
  const close = () => {
    toolbar.classList.add("hidden");
    selectedText = "";
  };
  const position = (rect) => {
    const width = toolbar.offsetWidth || 280;
    const left = Math.min(window.innerWidth - width - 12, Math.max(12, rect.left + rect.width / 2 - width / 2));
    const above = rect.top - toolbar.offsetHeight - 10;
    toolbar.style.left = `${Math.round(left)}px`;
    toolbar.style.top = `${Math.round(above > 8 ? above : rect.bottom + 10)}px`;
  };
  const showForSelection = () => {
    const selection = window.getSelection();
    const text = String(selection?.toString() || "").replace(/\s+/g, " ").trim();
    if (!selection || selection.rangeCount === 0 || text.length < 2) {
      close();
      return;
    }
    const range = selection.getRangeAt(0);
    const node = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? range.commonAncestorContainer
      : range.commonAncestorContainer.parentElement;
    const bubble = node?.closest?.(".msg.ai .bubble");
    if (!bubble || !messages.contains(bubble)) {
      close();
      return;
    }
    selectedText = text.slice(0, 1200);
    toolbar.classList.remove("hidden");
    position(range.getBoundingClientRect());
  };
  const onPointerUp = () => requestAnimationFrame(showForSelection);
  const onToolbarPointerDown = (event) => event.preventDefault();
  const onToolbarClick = (event) => {
    const button = event.target.closest("[data-selection-action]");
    if (!button || !selectedText) return;
    const instruction = ACTIONS[button.dataset.selectionAction];
    if (!instruction) return;
    input.value = `${instruction}\n\n> ${selectedText}`;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    window.getSelection()?.removeAllRanges();
    close();
  };
  const onDocumentPointerDown = (event) => {
    if (toolbar.classList.contains("hidden") || toolbar.contains(event.target)) return;
    close();
  };
  const onKeyDown = (event) => {
    if (event.key === "Escape") close();
  };
  const onScroll = () => close();

  messages.addEventListener("pointerup", onPointerUp);
  toolbar.addEventListener("pointerdown", onToolbarPointerDown);
  toolbar.addEventListener("click", onToolbarClick);
  document.addEventListener("pointerdown", onDocumentPointerDown);
  document.addEventListener("keydown", onKeyDown);
  messages.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });

  return () => {
    messages.removeEventListener("pointerup", onPointerUp);
    toolbar.removeEventListener("pointerdown", onToolbarPointerDown);
    toolbar.removeEventListener("click", onToolbarClick);
    document.removeEventListener("pointerdown", onDocumentPointerDown);
    document.removeEventListener("keydown", onKeyDown);
    messages.removeEventListener("scroll", onScroll);
    window.removeEventListener("resize", onScroll);
    toolbar.remove();
  };
}

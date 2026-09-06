/** Contextual source/action menu for the prompt composer. */

function _insertToken(input, token) {
  if (!input) return;
  const value = input.value || "";
  const start = input.selectionStart ?? value.length;
  const end = input.selectionEnd ?? start;
  const prefix = start > 0 && !/\s/.test(value[start - 1]) ? " " : "";
  const next = `${prefix}${token}`;
  input.value = `${value.slice(0, start)}${next}${value.slice(end)}`;
  const position = start + next.length;
  input.setSelectionRange(position, position);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
}

export function initComposerMenu({ button, input, onUpload, onBrowseFiles } = {}) {
  if (!button || !input) return () => {};

  const menu = document.createElement("div");
  menu.className = "composer-source-menu hidden";
  menu.id = "composer-source-menu";
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", "Add context or action");
  menu.innerHTML = `
    <div class="composer-source-menu-label">Add to prompt</div>
    <button type="button" class="composer-source-menu-item" role="menuitem" data-action="upload">
      <span class="composer-source-menu-icon" aria-hidden="true">↑</span>
      <span><strong>Upload files</strong><small>Documents, images, and other context</small></span>
    </button>
    <button type="button" class="composer-source-menu-item" role="menuitem" data-action="files">
      <span class="composer-source-menu-icon" aria-hidden="true">◇</span>
      <span><strong>Browse workspace files</strong><small>Use an existing generated file</small></span>
    </button>
    <div class="composer-source-menu-separator"></div>
    <button type="button" class="composer-source-menu-item" role="menuitem" data-action="skill">
      <span class="composer-source-menu-icon" aria-hidden="true">/</span>
      <span><strong>Use a skill</strong><small>Search available skills and commands</small></span>
      <kbd>/</kbd>
    </button>
    <button type="button" class="composer-source-menu-item" role="menuitem" data-action="agent">
      <span class="composer-source-menu-icon" aria-hidden="true">@</span>
      <span><strong>Mention an agent</strong><small>Route work to a specific agent</small></span>
      <kbd>@</kbd>
    </button>`;

  button.parentElement?.appendChild(menu);
  button.setAttribute("aria-haspopup", "menu");
  button.setAttribute("aria-controls", menu.id);
  button.setAttribute("aria-expanded", "false");

  const close = ({ restoreFocus = false } = {}) => {
    menu.classList.add("hidden");
    button.setAttribute("aria-expanded", "false");
    if (restoreFocus) button.focus();
  };
  const open = () => {
    menu.classList.remove("hidden");
    button.setAttribute("aria-expanded", "true");
    menu.querySelector(".composer-source-menu-item")?.focus();
  };
  const toggle = () => menu.classList.contains("hidden") ? open() : close({ restoreFocus: true });

  const onButtonClick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    toggle();
  };
  const onMenuClick = (event) => {
    const item = event.target.closest("[data-action]");
    if (!item) return;
    const action = item.dataset.action;
    close();
    if (action === "upload") onUpload?.();
    if (action === "files") onBrowseFiles?.();
    if (action === "skill") _insertToken(input, "/");
    if (action === "agent") _insertToken(input, "@");
  };
  const onDocumentPointerDown = (event) => {
    if (menu.classList.contains("hidden")) return;
    if (menu.contains(event.target) || button.contains(event.target)) return;
    close();
  };
  const onKeyDown = (event) => {
    if (menu.classList.contains("hidden")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close({ restoreFocus: true });
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(menu.querySelectorAll(".composer-source-menu-item"));
    const current = items.indexOf(document.activeElement);
    const delta = event.key === "ArrowDown" ? 1 : -1;
    items[(current + delta + items.length) % items.length]?.focus();
  };

  button.addEventListener("click", onButtonClick);
  menu.addEventListener("click", onMenuClick);
  document.addEventListener("pointerdown", onDocumentPointerDown);
  document.addEventListener("keydown", onKeyDown);

  return () => {
    button.removeEventListener("click", onButtonClick);
    menu.removeEventListener("click", onMenuClick);
    document.removeEventListener("pointerdown", onDocumentPointerDown);
    document.removeEventListener("keydown", onKeyDown);
    menu.remove();
  };
}

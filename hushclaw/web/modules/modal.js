/**
 * modal.js — lightweight shared modal helpers for consistent dialogs.
 *
 * Theming: styles use CSS variables from styles/theme-modes.css (data-theme / data-mode).
 */

let _overlay = null;
let _activeCleanup = null;
/** Optional: run when modal closes via Escape, backdrop, or header ✕ (e.g. openConfirm → false). */
let _backdropDismissHandler = null;
let _restoreFocusTarget = null;

function _ensureOverlay() {
  if (_overlay) return _overlay;
  const root = document.createElement("div");
  root.id = "app-modal-overlay";
  root.className = "app-modal-overlay hidden";
  root.innerHTML = `
    <div class="app-modal-card" role="dialog" aria-modal="true" aria-labelledby="app-modal-title" aria-live="polite">
      <div class="app-modal-accent" aria-hidden="true"></div>
      <div class="app-modal-header">
        <div class="app-modal-brand">
          <span class="app-modal-brand-mark" aria-hidden="true">
            <img src="/icon.svg" alt="" loading="eager" decoding="async">
          </span>
          <div class="app-modal-title-wrap">
            <div class="app-modal-kicker">HushClaw</div>
            <h3 class="app-modal-title" id="app-modal-title"></h3>
          </div>
        </div>
        <button type="button" class="app-modal-close icon-btn" id="app-modal-close" aria-label="Close">✕</button>
      </div>
      <div class="app-modal-body" id="app-modal-body"></div>
      <div class="app-modal-footer" id="app-modal-footer"></div>
    </div>
  `;
  document.body.appendChild(root);
  _overlay = root;
  return _overlay;
}

/**
 * @param {{ invokeDismiss?: boolean }} [options] — if invokeDismiss === false, do not run backdrop-dismiss handler (action buttons).
 */
function _closeCurrent(options = {}) {
  if (!_overlay) return;
  const invokeDismiss = options.invokeDismiss !== false;
  const handler = _backdropDismissHandler;
  _backdropDismissHandler = null;
  if (invokeDismiss && handler) {
    try {
      handler();
    } catch (_) {
      /* ignore */
    }
  }
  _overlay.classList.add("closing");
  const cleanup = _activeCleanup;
  _activeCleanup = null;
  const done = () => {
    _overlay.classList.remove("closing");
    _overlay.classList.add("hidden");
    if (cleanup) { try { cleanup(); } catch (_) { /* ignore */ } }
    const focusTarget = _restoreFocusTarget;
    _restoreFocusTarget = null;
    if (focusTarget?.isConnected && typeof focusTarget.focus === "function") focusTarget.focus();
  };
  // Fall back to instant hide if animation unsupported or reduced-motion
  const card = _overlay.querySelector(".app-modal-card");
  if (card && window.matchMedia("(prefers-reduced-motion: no-preference)").matches) {
    card.addEventListener("animationend", done, { once: true });
    setTimeout(done, 300); // safety fallback
  } else {
    done();
  }
}

function _openModal({
  title = "",
  body = "",
  bodyIsHtml = false,
  actions = [],
  closeOnBackdrop = true,
  onBackdropDismiss = null,
  wideCard = false,
  cardClass = "",
  blockEsc = false,
}) {
  const overlay = _ensureOverlay();
  _restoreFocusTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  // Remove prior listeners / footer so stacked openDialog/openConfirm calls do not leak handlers.
  if (_activeCleanup) {
    try {
      _activeCleanup();
    } catch (_) {
      /* ignore */
    }
    _activeCleanup = null;
  }
  _backdropDismissHandler = typeof onBackdropDismiss === "function" ? onBackdropDismiss : null;

  const card = overlay.querySelector(".app-modal-card");
  card.className = "app-modal-card";
  card.classList.toggle("app-modal-card--wide", Boolean(wideCard));
  for (const cls of String(cardClass || "").split(/\s+/)) {
    if (cls) card.classList.add(cls);
  }
  const titleEl = overlay.querySelector("#app-modal-title");
  const bodyEl = overlay.querySelector("#app-modal-body");
  const footerEl = overlay.querySelector("#app-modal-footer");
  const closeBtn = overlay.querySelector("#app-modal-close");

  titleEl.textContent = title || "";
  bodyEl.classList.toggle("app-modal-body--html", Boolean(bodyIsHtml));
  if (bodyIsHtml) bodyEl.innerHTML = body;
  else bodyEl.textContent = body || "";
  footerEl.innerHTML = "";

  const onKeydown = (ev) => {
    if (ev.key === "Escape") {
      if (blockEsc) { ev.preventDefault(); return; }
      _closeCurrent();
      return;
    }
    if (ev.key === "Tab") {
      const focusable = Array.from(card.querySelectorAll('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'));
      if (!focusable.length) return;
      const current = focusable.indexOf(document.activeElement);
      const next = ev.shiftKey
        ? (current <= 0 ? focusable.length - 1 : current - 1)
        : (current < 0 || current === focusable.length - 1 ? 0 : current + 1);
      ev.preventDefault();
      focusable[next].focus();
    }
  };

  actions.forEach((act, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    const parts = ["app-modal-btn"];
    parts.push(act.secondary ? "app-modal-btn--secondary" : "app-modal-btn--primary");
    if (act.danger) parts.push("app-modal-btn--danger");
    btn.className = parts.join(" ");
    btn.textContent = act.label || `Action ${idx + 1}`;
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (act.onClick) act.onClick();
    });
    footerEl.appendChild(btn);
  });

  const onOverlayClick = (ev) => {
    if (!closeOnBackdrop) return;
    if (!card.contains(ev.target)) _closeCurrent();
  };

  const onCloseClick = () => {
    _closeCurrent();
  };

  window.addEventListener("keydown", onKeydown);
  overlay.addEventListener("click", onOverlayClick);
  closeBtn.addEventListener("click", onCloseClick);

  _activeCleanup = () => {
    window.removeEventListener("keydown", onKeydown);
    overlay.removeEventListener("click", onOverlayClick);
    closeBtn.removeEventListener("click", onCloseClick);
    footerEl.innerHTML = "";
  };

  overlay.classList.remove("hidden");
  requestAnimationFrame(() => footerEl.querySelector("button")?.focus());
}

export function openConfirm({
  title = "Confirm",
  message = "",
  confirmText = "Confirm",
  cancelText = "Cancel",
  closeOnBackdrop = true,
  /** When true, primary button uses destructive styling (e.g. delete flows). */
  dangerConfirm = false,
}) {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    _openModal({
      title,
      body: message,
      bodyIsHtml: false,
      closeOnBackdrop,
      wideCard: false,
      cardClass: "ai-approval-card",
      onBackdropDismiss: () => settle(false),
      actions: [
        {
          label: cancelText,
          secondary: true,
          onClick: () => {
            _closeCurrent({ invokeDismiss: false });
            settle(false);
          },
        },
        {
          label: confirmText,
          secondary: false,
          danger: dangerConfirm,
          onClick: () => {
            _closeCurrent({ invokeDismiss: false });
            settle(true);
          },
        },
      ],
    });
  });
}

export function openChoiceModal({
  title = "Choose",
  message = "",
  actions = [],
  closeOnBackdrop = true,
}) {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    _openModal({
      title,
      body: message,
      bodyIsHtml: false,
      closeOnBackdrop,
      wideCard: false,
      onBackdropDismiss: () => settle("cancel"),
      actions: actions.map((action, idx) => ({
        label: action.label || `Action ${idx + 1}`,
        secondary: Boolean(action.secondary),
        danger: Boolean(action.danger),
        onClick: () => {
          _closeCurrent({ invokeDismiss: false });
          settle(action.value ?? idx);
        },
      })),
    });
  });
}

export function openDialog({
  title = "",
  html = "",
  actions = [],
  closeOnBackdrop = true,
  onOpen = null,
  onClose = null,
  cardClass = "",
}) {
  _openModal({
    title,
    body: html,
    bodyIsHtml: true,
    closeOnBackdrop,
    wideCard: true,
    cardClass,
    actions: actions.map((a) => ({
      label: a.label,
      secondary: Boolean(a.secondary),
      danger: Boolean(a.danger),
      onClick: () => {
        if (a.onClick) a.onClick();
      },
    })),
  });
  if (typeof onClose === "function") {
    const prevCleanup = _activeCleanup;
    _activeCleanup = () => {
      if (prevCleanup) prevCleanup();
      try {
        onClose();
      } catch (_) {
        /* ignore */
      }
    };
  }
  if (typeof onOpen === "function") {
    try {
      onOpen();
    } catch (_) {
      /* ignore */
    }
  }
  return _closeCurrent;
}

export function closeModal() {
  _closeCurrent({ invokeDismiss: false });
}

/**
 * Open a non-dismissible modal for long-running operations (e.g. server upgrade).
 * The close button and backdrop are disabled while the operation runs.
 *
 * Returns a handle:
 *   handle.update(html)               — replace the body with new HTML
 *   handle.settle({ html, actions })  — finalize: re-enable close, show result + buttons
 */
export function openLiveModal({ title = "", html = "" } = {}) {
  _openModal({
    title,
    body: html,
    bodyIsHtml: true,
    closeOnBackdrop: false,
    blockEsc: true,
    wideCard: false,
    actions: [],
  });

  // Hide the ✕ button while the operation is in progress.
  const closeBtn = document.getElementById("app-modal-close");
  if (closeBtn) closeBtn.style.visibility = "hidden";

  return {
    update(newHtml) {
      const bodyEl = document.getElementById("app-modal-body");
      if (bodyEl) bodyEl.innerHTML = newHtml;
    },
    settle({ html: finalHtml = "", actions: finalActions = [] } = {}) {
      // Re-show ✕ and re-enable backdrop / ESC by swapping to a regular dialog.
      _openModal({
        title,
        body: finalHtml,
        bodyIsHtml: true,
        closeOnBackdrop: true,
        blockEsc: false,
        wideCard: false,
        actions: finalActions.map((a) => ({
          label: a.label,
          secondary: Boolean(a.secondary),
          danger: Boolean(a.danger),
          onClick: () => {
            _closeCurrent({ invokeDismiss: false });
            if (a.onClick) a.onClick();
          },
        })),
      });
    },
  };
}

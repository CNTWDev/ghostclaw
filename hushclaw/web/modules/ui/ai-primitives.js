/**
 * ai-primitives.js — the small, shared interaction contract for AI work.
 *
 * Product features may choose their own copy and layout, but they must expose
 * the same run states. This keeps chat, skills, tools, tasks, and approvals
 * visually coherent without forcing every feature into one large component.
 */

export const AI_STATES = Object.freeze({
  IDLE: "idle",
  QUEUED: "queued",
  RUNNING: "running",
  WAITING_USER: "waiting_user",
  STREAMING: "streaming",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELLED: "cancelled",
});

const STATE_ALIASES = Object.freeze({
  pending: AI_STATES.QUEUED,
  claimed: AI_STATES.QUEUED,
  working: AI_STATES.RUNNING,
  thinking: AI_STATES.RUNNING,
  waiting: AI_STATES.WAITING_USER,
  awaiting_user_confirmation: AI_STATES.WAITING_USER,
  done: AI_STATES.COMPLETED,
  complete: AI_STATES.COMPLETED,
  success: AI_STATES.COMPLETED,
  error: AI_STATES.FAILED,
  blocked: AI_STATES.WAITING_USER,
  stale: AI_STATES.FAILED,
  stopped: AI_STATES.CANCELLED,
});

export const ACTIVE_AI_STATES = new Set([
  AI_STATES.QUEUED,
  AI_STATES.RUNNING,
  AI_STATES.WAITING_USER,
  AI_STATES.STREAMING,
]);

let _processDisclosureId = 0;

export function normalizeAiState(value, fallback = AI_STATES.IDLE) {
  const raw = String(value || "").trim().toLowerCase().replace(/[ -]+/g, "_");
  if (!raw) return fallback;
  if (Object.values(AI_STATES).includes(raw)) return raw;
  return STATE_ALIASES[raw] || fallback;
}

export function applyAiState(element, value, { label = "" } = {}) {
  if (!element) return AI_STATES.IDLE;
  const state = normalizeAiState(value);
  element.dataset.aiState = state;
  element.setAttribute("aria-busy", String(state === AI_STATES.RUNNING || state === AI_STATES.STREAMING));
  if (label) element.setAttribute("aria-label", label);
  return state;
}

export function formatElapsed(startedAt, now = Date.now()) {
  const start = Number(startedAt || 0);
  const seconds = start > 0 ? Math.max(0, Math.floor((Number(now) - start) / 1000)) : 0;
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return `${minutes}m ${String(remaining).padStart(2, "0")}s`;
}

export function createAgentActivity({
  label = "Working…",
  detail = "",
  state = AI_STATES.RUNNING,
  startedAt = Date.now(),
} = {}) {
  const root = document.createElement("span");
  root.className = "ai-activity thinking-layout";
  root.setAttribute("role", "status");
  root.setAttribute("aria-live", "polite");
  root.innerHTML = `
    <span class="ai-activity-mark thinking-orb" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="ai-activity-copy thinking-copy"></span>
    <span class="ai-activity-detail"></span>
    <span class="ai-activity-elapsed thinking-elapsed"></span>`;

  const update = (next = {}) => {
    if (Object.hasOwn(next, "label")) label = String(next.label || "Working…");
    if (Object.hasOwn(next, "detail")) detail = String(next.detail || "");
    if (Object.hasOwn(next, "state")) state = normalizeAiState(next.state, AI_STATES.RUNNING);
    if (Object.hasOwn(next, "startedAt")) startedAt = Number(next.startedAt || Date.now());
    applyAiState(root, state, { label });
    root.querySelector(".ai-activity-copy").textContent = label;
    const detailEl = root.querySelector(".ai-activity-detail");
    detailEl.textContent = detail;
    detailEl.hidden = !detail;
    const elapsedEl = root.querySelector(".ai-activity-elapsed");
    elapsedEl.textContent = ACTIVE_AI_STATES.has(state) ? formatElapsed(startedAt) : "";
    elapsedEl.hidden = !ACTIVE_AI_STATES.has(state);
  };

  root.updateActivity = update;
  update();
  return root;
}

export function createProcessDisclosure({
  index = "",
  label = "Processing…",
  state = AI_STATES.RUNNING,
  expanded = false,
} = {}) {
  const root = document.createElement("div");
  root.className = `tool-round compact-process ai-process-disclosure${expanded ? "" : " collapsed"}`;

  const header = document.createElement("div");
  header.className = "tool-round-header round-line ai-process-header";
  header.setAttribute("role", "button");
  header.setAttribute("tabindex", "0");
  header.setAttribute("aria-expanded", String(expanded));

  const indexEl = document.createElement("span");
  indexEl.className = "round-index ai-process-index";
  indexEl.textContent = index;
  indexEl.hidden = !index;

  const stateMark = document.createElement("span");
  stateMark.className = "ai-process-state";
  stateMark.setAttribute("aria-hidden", "true");

  const summary = document.createElement("span");
  summary.className = "tr-summary ai-process-summary";
  summary.textContent = label;

  const toggle = document.createElement("span");
  toggle.className = "tr-toggle ai-process-toggle";
  toggle.setAttribute("aria-hidden", "true");
  header.append(indexEl, stateMark, summary, toggle);

  const viewport = document.createElement("div");
  viewport.className = "ai-process-viewport";
  viewport.id = `ai-process-${++_processDisclosureId}`;
  header.setAttribute("aria-controls", viewport.id);
  const body = document.createElement("div");
  body.className = "tool-round-body ai-process-body";
  viewport.appendChild(body);
  root.append(header, viewport);

  const setExpanded = (value) => {
    const isExpanded = Boolean(value);
    root.classList.toggle("collapsed", !isExpanded);
    header.setAttribute("aria-expanded", String(isExpanded));
  };
  const toggleExpanded = () => setExpanded(root.classList.contains("collapsed"));
  header.addEventListener("click", toggleExpanded);
  header.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    toggleExpanded();
  });

  const update = (next = {}) => {
    if (Object.hasOwn(next, "label")) summary.textContent = String(next.label || "Processing…");
    if (Object.hasOwn(next, "state")) state = normalizeAiState(next.state, AI_STATES.RUNNING);
    if (Object.hasOwn(next, "index")) {
      indexEl.textContent = String(next.index || "");
      indexEl.hidden = !indexEl.textContent;
    }
    if (Object.hasOwn(next, "expanded")) setExpanded(next.expanded);
    applyAiState(root, state, { label: summary.textContent });
  };

  root.updateProcess = update;
  root.setExpanded = setExpanded;
  update();
  return { root, header, body, summary };
}

export function createContextCard({ label = "Source", title = "", detail = "", meta = "" } = {}) {
  const card = document.createElement("article");
  card.className = "ai-context-card";
  card.innerHTML = `
    <span class="ai-context-label"></span>
    <span class="ai-context-content">
      <strong class="ai-context-title"></strong>
      <span class="ai-context-detail"></span>
    </span>
    <span class="ai-context-meta"></span>`;
  card.querySelector(".ai-context-label").textContent = label;
  card.querySelector(".ai-context-title").textContent = title;
  card.querySelector(".ai-context-detail").textContent = detail;
  card.querySelector(".ai-context-meta").textContent = meta;
  card.querySelector(".ai-context-detail").hidden = !detail;
  card.querySelector(".ai-context-meta").hidden = !meta;
  return card;
}

/**
 * chat.js — Chat message rendering, thinking indicator, session history.
 *
 * Tool-related rendering lives in chat/tools.js.
 * Copy/export/share actions live in chat/export.js.
 */

import {
  state, els, escHtml,
  isSessionRunning, setCurrentSessionId, clearCurrentSessionId, getCurrentSessionId, debugUiLifecycle,
} from "./state.js";
import { setMarkdownContent } from "./markdown.js";
import { refreshChatStats } from "./stats.js";

import {
  resetActiveRound, finalizeActiveRound, renderToolResult,
} from "./chat/tools.js";
import { addCopyActions } from "./chat/export.js";
import { AI_STATES, applyAiState, createAgentActivity } from "./ui/ai-primitives.js";

// Re-export everything consumers need from the submodules, keeping the public
// surface of chat.js unchanged.
export {
  isDevMode, setDevMode,
  insertToolBubble, updateToolBubble, renderToolResult,
  finalizeActiveRound, createToolRound, insertRoundLine,
} from "./chat/tools.js";

export {
  addCopyActions, exportCurrentSessionAsPdf,
} from "./chat/export.js";

let _streamRenderQueued = false;
let _streamRenderTimer = 0;
let _streamActivityTimer = null;
let _streamBufferedChars = 0;
let _streamLastRenderTs = 0;
const _userMsgElsByClientTurn = new Map();
const _aiMsgElsByClientTurn = new Map();
const _STREAM_RENDER_MIN_MS = 32;
const _STREAM_RENDER_MIN_CHARS = 48;
const _HISTORY_WINDOW_THRESHOLD = 120;
const _HISTORY_REAL_TAIL_TURNS = 48;
const _HISTORY_PREPEND_CHUNK_SIZE = 24;
const _HISTORY_PREPEND_TRIGGER_PX = 220;
const _chatPerf = {
  lastInputTs: 0,
  pendingScrollTs: 0,
  pendingScrollIdleMs: 0,
};

function _chatPerfEnabled() {
  return typeof window !== "undefined" && window.__HUSHCLAW_DEBUG_PERF === true;
}

function _chatPerfPush(name, data = {}) {
  // High-frequency stream/scroll markers stay opt-in so diagnostics never
  // compete with the actual chat interaction in normal production use.
  if (!_chatPerfEnabled()) return;
  const entry = { name, ts: performance.now(), ...data };
  const history = Array.isArray(window.__HC_CHAT_PERF) ? window.__HC_CHAT_PERF : [];
  history.push(entry);
  if (history.length > 120) history.splice(0, history.length - 120);
  window.__HC_CHAT_PERF = history;
}
function _chatPerfMarkInput(name, data = {}) {
  if (!_chatPerfEnabled()) return;
  _chatPerf.lastInputTs = performance.now();
  _chatPerfPush(`input-${name}`, data);
}
function _chatPerfPushViewport(name, data = {}) {
  _chatPerfPush(`viewport-${name}`, data);
}
function _initChatPerf() {
  _chatPerfPush("chat-init");
}

function _turnDate(t) {
  const raw = Number(t?.ts || 0);
  if (!Number.isFinite(raw) || raw <= 0) return new Date();
  // Legacy turns use epoch seconds; event-log replay uses epoch milliseconds.
  const ms = raw < 1_000_000_000_000 ? raw * 1000 : raw;
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? new Date() : d;
}
let _lastMarkdownRenderTs = 0;
let _activeRoundLabel = "";

function _clearStreamTimers() {
  if (_streamRenderTimer) {
    clearTimeout(_streamRenderTimer);
    _streamRenderTimer = 0;
  }
  if (_streamActivityTimer) {
    clearTimeout(_streamActivityTimer);
    _streamActivityTimer = null;
  }
}

function _clearClientTurnBindings() {
  _userMsgElsByClientTurn.clear();
  _aiMsgElsByClientTurn.clear();
}

function _bindClientTurnMessage(role, clientTurnId, msgEl) {
  const turnId = String(clientTurnId || "").trim();
  if (!turnId || !msgEl) return;
  msgEl.dataset.clientTurnId = turnId;
  if (role === "user") _userMsgElsByClientTurn.set(turnId, msgEl);
  if (role === "ai") _aiMsgElsByClientTurn.set(turnId, msgEl);
}

function _messageStatePill(msgEl, metaEl, label, stateKey = "") {
  if (!msgEl || !metaEl) return null;
  let pill = metaEl.querySelector(".msg-state-pill");
  if (!pill) {
    pill = document.createElement("span");
    pill.className = "msg-state-pill";
    metaEl.appendChild(pill);
  }
  pill.textContent = String(label || "").trim();
  if (stateKey) pill.dataset.state = stateKey;
  else delete pill.dataset.state;
  return pill;
}

function _setUserMessageQueued(clientTurnId, queued) {
  const turnId = String(clientTurnId || "").trim();
  if (!turnId) return;
  const msgEl = _userMsgElsByClientTurn.get(turnId);
  if (!msgEl?.isConnected) {
    _userMsgElsByClientTurn.delete(turnId);
    return;
  }
  const metaEl = msgEl.querySelector(".msg-meta");
  const existing = metaEl?.querySelector('.msg-state-pill[data-state="queued"]');
  if (queued) {
    _messageStatePill(msgEl, metaEl, "queued", "queued");
    msgEl.dataset.deliveryState = "queued";
    return;
  }
  if (existing) existing.remove();
  if (!metaEl?.querySelector(".msg-state-pill")) delete msgEl.dataset.deliveryState;
}

function _finalizeAiBubbleMarkdown(bubbleEl) {
  if (!bubbleEl || !bubbleEl._streamingMarkdown) return;
  const raw = bubbleEl._raw || "";
  bubbleEl._streamingMarkdown = false;
  if (bubbleEl._finalizeMarkdownScheduled) return;
  bubbleEl._finalizeMarkdownScheduled = true;
  const started = performance.now();
  _chatPerfPush("markdown-finalize-start", { rawLength: raw.length });
  if (bubbleEl?.isConnected) {
    // React/Streamdown already owns the final DOM from the throttled
    // streaming renders. Re-rendering in static mode here parses the whole
    // answer a second time and creates the visible "cursor waiting" gap.
    // Keep the existing Markdown tree and only flip its state marker. The
    // native fallback still needs one final pass to remove streaming repairs.
    if (bubbleEl.dataset.renderer === "react") {
      bubbleEl.querySelector(".react-markdown-surface")?.setAttribute("data-streaming", "false");
    } else {
      setMarkdownContent(bubbleEl, raw, { surface: "chat", className: "bubble markdown-body" });
    }
    _chatPerfPush("markdown-finalize-complete", {
      rawLength: raw.length,
      durationMs: Math.round(performance.now() - started),
      reusedStreamingTree: bubbleEl.dataset.renderer === "react",
    });
  }
  bubbleEl._finalizeMarkdownScheduled = false;
}

function _setAiStreamingState(active) {
  const msgEl = state._aiMsgEl;
  const bubbleEl = state._aiBubbleEl;
  if (!msgEl || !bubbleEl) return;
  msgEl.classList.toggle("msg-streaming", active);
  bubbleEl.classList.toggle("bubble-streaming", active);
  applyAiState(msgEl, active ? AI_STATES.STREAMING : AI_STATES.COMPLETED);
  if (!active) {
    bubbleEl.classList.remove("bubble-chunk-active");
  }
}

function _animateStreamChunk(bubbleEl) {
  if (!bubbleEl) return;
  bubbleEl.classList.add("bubble-chunk-active");
  _clearStreamTimers();
  _streamActivityTimer = setTimeout(() => {
    bubbleEl.classList.remove("bubble-chunk-active");
    _streamActivityTimer = null;
  }, 1200);
}

function _renderAiBubbleNow() {
  _streamRenderQueued = false;
  const bubbleEl = state._aiBubbleEl;
  if (!bubbleEl) return;

  const raw = bubbleEl._raw || "";
  const t0 = performance.now();
  setMarkdownContent(bubbleEl, raw, { surface: "chat", streaming: true, className: "bubble markdown-body" });
  _lastMarkdownRenderTs = Date.now();
  _streamLastRenderTs = performance.now();
  _streamBufferedChars = 0;
  _animateStreamChunk(bubbleEl);
  _chatPerfPush("stream-render", {
    renderMs: Math.round((performance.now() - t0) * 10) / 10,
    rawLength: raw.length,
  });
  _scrollToBottomIfAuto();
}

function _scheduleAiBubbleRender(delayMs = 0) {
  if (_streamRenderTimer) return;
  const schedule = () => {
    _streamRenderTimer = 0;
    if (_streamRenderQueued) return;
    _streamRenderQueued = true;
    requestAnimationFrame(_renderAiBubbleNow);
  };
  if (delayMs <= 0) {
    schedule();
    return;
  }
  _streamRenderTimer = setTimeout(schedule, delayMs);
}

function _queueAiBubbleRender(force = false) {
  const rawLength = state._aiBubbleEl?._raw?.length || 0;
  const now = performance.now();
  const sinceLastRender = _streamLastRenderTs ? now - _streamLastRenderTs : Number.POSITIVE_INFINITY;
  const shouldFlushNow = force || _streamBufferedChars >= _STREAM_RENDER_MIN_CHARS || sinceLastRender >= _STREAM_RENDER_MIN_MS;
  _chatPerfPush("stream-render-queued", {
    rawLength,
    force,
    bufferedChars: _streamBufferedChars,
    delayMs: shouldFlushNow ? 0 : Math.max(0, Math.round(_STREAM_RENDER_MIN_MS - sinceLastRender)),
  });
  if (shouldFlushNow) {
    _scheduleAiBubbleRender(0);
    return;
  }
  _scheduleAiBubbleRender(Math.max(0, _STREAM_RENDER_MIN_MS - sinceLastRender));
}

function _flushAiBubbleRender() {
  _clearStreamTimers();
  if (_streamRenderQueued && state._aiBubbleEl) {
    _renderAiBubbleNow();
    return;
  }
  if ((_streamBufferedChars > 0 || state._aiBubbleEl?._raw) && state._aiBubbleEl) {
    _streamRenderQueued = true;
    _renderAiBubbleNow();
  }
}

function _finishAiMessageNow() {
  _flushAiBubbleRender();
  removeThinkingMsg();
  finalizeActiveRound();
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  if (state._aiBubbleEl) {
    _clearStreamTimers();
    _finalizeAiBubbleMarkdown(state._aiBubbleEl);
    _setAiStreamingState(false);
  }
  state._aiMsgEl = null;
  state._aiBubbleEl = null;
  _streamRenderQueued = false;
  _streamBufferedChars = 0;
  _streamLastRenderTs = 0;
  _lastMarkdownRenderTs = 0;
  _autoScroll = true;
  _updateJumpBtn();
  _chatPerfPush("stream-finished");
}

// Show / hide all share-forum buttons when auth state changes.
document.addEventListener("hc:forum-ready", () => {
  document.querySelectorAll(".share-forum-btn").forEach(b => { b.style.display = ""; });
});
document.addEventListener("hc:forum-unauthed", () => {
  document.querySelectorAll(".share-forum-btn").forEach(b => { b.style.display = "none"; });
});

// ── Smart auto-scroll ──────────────────────────────────────────────────────
let _autoScroll = true;        // false = user scrolled up during streaming
const _SCROLL_THRESHOLD = 80;  // px from bottom to count as "at bottom"
const _HISTORY_BOTTOM_REVEAL_IDLE_MS = 180;
const _HISTORY_BOTTOM_REVEAL_MAX_MS = 3000;
let _lastTouchY = 0;
let _historyBottomReveal = null;
let _scrollStateRaf = 0;
let _historyWindow = null;
let _historyWindowSyncRaf = 0;
let _messagesStage = null;
let _sessionHistoryRenderNonce = 0;

function _createMessagesBottomSentinel() {
  const sentinel = document.createElement("div");
  sentinel.className = "messages-bottom-sentinel";
  sentinel.setAttribute("aria-hidden", "true");
  sentinel.style.cssText = "width:100%;height:1px;pointer-events:none;";
  return sentinel;
}

const _messagesBottomSentinel = _createMessagesBottomSentinel();

function _ensureMessagesStage() {
  if (_messagesStage) return _messagesStage;
  const hostParent = els.messages?.parentElement;
  if (!hostParent) return null;
  const shell = document.createElement("div");
  shell.className = "messages-shell";
  hostParent.insertBefore(shell, els.messages);
  shell.appendChild(els.messages);
  const stage = document.createElement("div");
  stage.className = "messages-stage";
  stage.setAttribute("aria-hidden", "true");
  shell.appendChild(stage);
  _messagesStage = stage;
  return stage;
}

function _isNearBottom() {
  const el = els.messages;
  return el.scrollHeight - el.scrollTop - el.clientHeight < _SCROLL_THRESHOLD;
}

function _updateJumpBtn() {
  const btn = document.getElementById("scroll-jump-btn");
  if (!btn) return;
  btn.hidden = _autoScroll || !state._aiMsgEl;
}

function _scrollToBottomIfAuto() {
  if (_autoScroll) _alignMessagesToBottom("auto");
  _updateJumpBtn();
}

function _cancelHistoryBottomReveal() {
  const active = _historyBottomReveal;
  if (!active) return;
  _historyBottomReveal = null;
  if (active.raf) cancelAnimationFrame(active.raf);
  if (active.idleTimer) clearTimeout(active.idleTimer);
  if (active.mutationObserver) active.mutationObserver.disconnect();
  _chatPerfPush("history-bottom-reveal-cancel");
}

function _ensureMessagesBottomSentinel(host = els.messages) {
  if (!host) return null;
  const sentinel = host === els.messages
    ? _messagesBottomSentinel
    : (host._messagesBottomSentinel ||= _createMessagesBottomSentinel());
  if (sentinel.parentElement !== host || host.lastElementChild !== sentinel) {
    host.appendChild(sentinel);
  }
  return sentinel;
}

function _alignHostToBottom(host) {
  if (!host) return;
  _ensureMessagesBottomSentinel(host);
  host.scrollTop = host.scrollHeight;
}

function _alignMessagesToBottom(reason = "unknown") {
  _alignHostToBottom(els.messages);
  _chatPerfPushViewport("align-bottom", { reason });
}

function _scheduleHistoryBottomRevealSettle(active) {
  if (!active || _historyBottomReveal !== active) return;
  if (active.idleTimer) clearTimeout(active.idleTimer);
  active.idleTimer = setTimeout(() => {
    if (_historyBottomReveal !== active) return;
    _alignMessagesToBottom("history-settle");
    _chatPerfPush("history-bottom-reveal-settle");
    _cancelHistoryBottomReveal();
  }, _HISTORY_BOTTOM_REVEAL_IDLE_MS);
}

function _scheduleHistoryBottomAlign(active) {
  if (!active || _historyBottomReveal !== active || active.raf) return;
  active.raf = requestAnimationFrame(() => {
    active.raf = 0;
    if (_historyBottomReveal !== active) return;
    if (active.sessionId !== getCurrentSessionId()) {
      _cancelHistoryBottomReveal();
      return;
    }
    _alignMessagesToBottom("history-align");
    _scheduleHistoryBottomRevealSettle(active);
    if (performance.now() >= active.deadline) {
      _cancelHistoryBottomReveal();
      return;
    }
  });
}

function _startHistoryBottomReveal(sessionId) {
  if (!sessionId || !els.messages) return;
  _cancelHistoryBottomReveal();
  _autoScroll = true;
  _updateJumpBtn();
  const active = {
    sessionId,
    deadline: performance.now() + _HISTORY_BOTTOM_REVEAL_MAX_MS,
    raf: 0,
    idleTimer: 0,
    mutationObserver: null,
  };
  _historyBottomReveal = active;
  if (typeof MutationObserver === "function") {
    active.mutationObserver = new MutationObserver(() => {
      if (_historyBottomReveal !== active) return;
      _chatPerfPush("history-bottom-reveal-mutation");
      _scheduleHistoryBottomAlign(active);
    });
    active.mutationObserver.observe(els.messages, { childList: true });
  }
  _chatPerfPush("history-bottom-reveal-start", { targetSessionId: sessionId });
  _scheduleHistoryBottomAlign(active);
}

function _applyMessagesScrollState() {
  const now = performance.now();
  const latencyMs = _chatPerf.pendingScrollTs ? Math.round(now - _chatPerf.pendingScrollTs) : 0;
  const idleMs = _chatPerf.pendingScrollIdleMs || 0;
  _scrollStateRaf = 0;
  if (_isNearBottom()) {
    _autoScroll = true;
  } else if (state._aiMsgEl) {
    _autoScroll = false;  // pause only while streaming
  }
  _updateJumpBtn();
  _chatPerfPush("scroll-state", { latencyMs, idleMs });
  _chatPerf.pendingScrollTs = 0;
  _chatPerf.pendingScrollIdleMs = 0;
}

function _pauseAutoScrollForUserIntent() {
  if (!state._aiMsgEl) return;
  if (_isNearBottom()) return;
  _autoScroll = false;
  _updateJumpBtn();
}

els.messages.addEventListener("wheel", (ev) => {
  _chatPerfMarkInput("wheel", { deltaY: Math.round(ev.deltaY || 0) });
  if (ev.deltaY < 0) {
    _cancelHistoryBottomReveal();
    _autoScroll = false;
    _updateJumpBtn();
  }
}, { passive: true });

els.messages.addEventListener("touchstart", (ev) => {
  _chatPerfMarkInput("touchstart");
  _lastTouchY = ev.touches?.[0]?.clientY || 0;
}, { passive: true });

els.messages.addEventListener("touchmove", (ev) => {
  _chatPerfMarkInput("touchmove");
  const y = ev.touches?.[0]?.clientY || 0;
  if (y > _lastTouchY) {
    _cancelHistoryBottomReveal();
    _autoScroll = false;
    _updateJumpBtn();
  } else {
    _pauseAutoScrollForUserIntent();
  }
  _lastTouchY = y;
}, { passive: true });

els.messages.addEventListener("keydown", (ev) => {
  if (!["ArrowUp", "PageUp", "Home"].includes(ev.key) && !(ev.key === " " && ev.shiftKey)) return;
  _chatPerfMarkInput("messages-keydown", { key: ev.key });
  _cancelHistoryBottomReveal();
  _pauseAutoScrollForUserIntent();
});

els.messages.addEventListener("scroll", () => {
  if (_chatPerfEnabled()) {
    const now = performance.now();
    _chatPerf.pendingScrollTs = now;
    _chatPerf.pendingScrollIdleMs = _chatPerf.lastInputTs ? Math.round(now - _chatPerf.lastInputTs) : 0;
    _chatPerfPush("scroll-event", {
      idleMs: _chatPerf.pendingScrollIdleMs,
      nearBottom: _isNearBottom(),
    });
  }
  _scheduleHistoryWindowSync();
  if (_scrollStateRaf) return;
  _scrollStateRaf = requestAnimationFrame(_applyMessagesScrollState);
}, { passive: true });

// Jump button — anchored to the main chat column so it stays aligned with
// the message lane even when the right-side workbench is open.
(() => {
  _ensureMessagesStage();
  const btn = document.createElement("button");
  btn.id = "scroll-jump-btn";
  btn.hidden = true;
  btn.textContent = "↓ Jump to bottom";
  btn.addEventListener("click", () => { _autoScroll = true; scrollToBottom(); });
  (els.chatMainColumn || els.chatArea).appendChild(btn);
})();
// ──────────────────────────────────────────────────────────────────────────

// ── Scrolling ──────────────────────────────────────────────────────────────

export function scrollToBottom() {
  _cancelHistoryBottomReveal();
  _autoScroll = true;
  _alignMessagesToBottom("manual");
  _updateJumpBtn();
  _chatPerfPush("scroll-to-bottom-call");
}

function _scheduleHistoryWindowSync() {
  if (!_historyWindow || _historyWindowSyncRaf) return;
  _historyWindowSyncRaf = requestAnimationFrame(() => {
    _historyWindowSyncRaf = 0;
    _syncHistoryWindow(_historyWindow);
  });
}

function _clearHistoryWindow() {
  if (_historyWindowSyncRaf) {
    cancelAnimationFrame(_historyWindowSyncRaf);
    _historyWindowSyncRaf = 0;
  }
  _historyWindow = null;
}

function _renderTurnsRange(turns, start, end, parent = els.messages) {
  const frag = parent instanceof DocumentFragment ? parent : document.createDocumentFragment();
  for (let i = start; i < end; i++) {
    const turnStart = performance.now();
    _renderOneTurn(turns[i], frag);
    _chatPerfPush("history-render-turn", {
      index: i,
      role: turns[i]?.role || "",
      renderMs: Math.round((performance.now() - turnStart) * 10) / 10,
    });
  }
  if (frag !== parent) parent.appendChild(frag);
}

function _prependOlderHistoryChunk(win = _historyWindow) {
  const host = win?.host || els.messages;
  if (!win || win.isPrepending || win.nextIndex <= 0 || !host) return;
  win.isPrepending = true;
  const beforeHeight = host.scrollHeight;
  const beforeTop = host.scrollTop;
  const start = Math.max(0, win.nextIndex - _HISTORY_PREPEND_CHUNK_SIZE);
  const end = win.nextIndex;
  const frag = document.createDocumentFragment();

  if (start === 0 && !win.headersPrepended) {
    _renderSessionSummary(win.summary, frag);
    _renderSessionLineage(win.lineage, frag);
    win.headersPrepended = true;
  }
  _renderTurnsRange(win.turns, start, end, frag);

  const firstNode = host.firstChild;
  if (firstNode) host.insertBefore(frag, firstNode);
  else host.appendChild(frag);
  _ensureMessagesBottomSentinel(host);

  const afterHeight = host.scrollHeight;
  const delta = Math.max(0, afterHeight - beforeHeight);
  host.scrollTop = beforeTop + delta;
  win.nextIndex = start;
  win.isPrepending = false;
  if (win.nextIndex <= 0) win.complete = true;
  _chatPerfPushViewport("history-prepend-complete", {
    prependedStart: start,
    prependedEnd: end,
    prependedTurns: end - start,
    remainingTurns: win.nextIndex,
  });
}

function _syncHistoryWindow(win = _historyWindow) {
  const host = win?.host || els.messages;
  if (!win || !host) return;
  if (host.classList.contains("history-preparing")) return;
  if (host.scrollTop > _HISTORY_PREPEND_TRIGGER_PX) return;
  _prependOlderHistoryChunk(win);
}

function _mountWindowedHistory(turns, { summary = "", lineage = [], host = els.messages } = {}) {
  const splitIndex = Math.max(0, turns.length - _HISTORY_REAL_TAIL_TURNS);
  const win = {
    host,
    turns,
    nextIndex: splitIndex,
    summary,
    lineage,
    headersPrepended: false,
    isPrepending: false,
    complete: splitIndex <= 0,
  };
  _renderTurnsRange(turns, splitIndex, turns.length, host);
  _ensureMessagesBottomSentinel(host);
  _chatPerfPush("history-window-init", {
    turnCount: turns.length,
    initialTailTurns: turns.length - splitIndex,
    remainingTurns: splitIndex,
  });
  return win;
}

function _nextFrame() {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

async function _waitForHostHeightStable(host, { stableFrames = 3, maxFrames = 24 } = {}) {
  let framesLeft = maxFrames;
  let stable = 0;
  let lastHeight = -1;
  while (framesLeft-- > 0) {
    await _nextFrame();
    const height = Math.round(host?.scrollHeight || 0);
    if (height === lastHeight) stable += 1;
    else stable = 0;
    lastHeight = height;
    if (stable >= stableFrames) break;
  }
}

async function _prepareHistoryStage(host, turnList, { summary = "", lineage = [], keepInProgress = false } = {}) {
  const useWindowedHistory = !keepInProgress && turnList.length >= _HISTORY_WINDOW_THRESHOLD;
  host.innerHTML = "";
  host.classList.add("no-msg-anim", "history-preparing", "history-measuring");
  let win = null;
  if (!useWindowedHistory) {
    _renderSessionSummary(summary, host);
    _renderSessionLineage(lineage, host);
  }
  if (useWindowedHistory) {
    win = _mountWindowedHistory(turnList, { summary, lineage, host });
  } else {
    _renderTurnsRange(turnList, 0, turnList.length, host);
    _ensureMessagesBottomSentinel(host);
  }
  _alignHostToBottom(host);
  await _waitForHostHeightStable(host);
  _alignHostToBottom(host);
  await _waitForHostHeightStable(host, { stableFrames: 2, maxFrames: 10 });
  host.classList.remove("history-measuring", "history-preparing", "no-msg-anim");
  return { useWindowedHistory, win };
}

function _swapPreparedStageIntoView(stageHost, win = null) {
  const children = Array.from(stageHost.childNodes);
  els.messages.replaceChildren(...children);
  _ensureMessagesBottomSentinel(els.messages);
  _historyWindow = win ? { ...win, host: els.messages } : null;
  _alignMessagesToBottom("history-swap");
}

async function _finalizeHistoryInitialViewport(stageHost, { keepInProgress = false, historyWindow = null } = {}) {
  _swapPreparedStageIntoView(stageHost, historyWindow);
  if (keepInProgress) rehydrateInProgressUi(getCurrentSessionId());
  _updateJumpBtn();
  await _nextFrame();
  els.chatArea.classList.remove("session-switching");
  els.chatArea.classList.add("session-swap-in");
  requestAnimationFrame(() => {
    els.chatArea.classList.remove("session-swap-in");
  });
}

export function noteSessionSwitchRequested(sessionId) {
  _chatPerfPushViewport("session-switch-request", {
    requestedSessionId: sessionId || "",
    currentSessionId: getCurrentSessionId() || "",
  });
}

export function noteSessionHistoryReceived(sessionId, turnCount, { summary = false, lineageCount = 0 } = {}) {
  _chatPerfPushViewport("session-history-received", {
    receivedSessionId: sessionId || "",
    turnCount: Number(turnCount || 0),
    hasSummary: !!summary,
    lineageCount: Number(lineageCount || 0),
  });
}

// ── Message bubble factory ─────────────────────────────────────────────────

export function createMsgBubble(kind) {
  const msgEl = document.createElement("div");
  msgEl.className = `msg ${kind}`;
  msgEl.dataset.role = kind;

  const innerEl = document.createElement("div");
  innerEl.className = "msg-inner";

  const avatarEl = document.createElement("span");
  avatarEl.className = "msg-avatar";
  if (kind === "user") avatarEl.textContent = "You";
  else if (kind === "ai") {
    avatarEl.setAttribute("aria-label", "Pip");
    avatarEl.innerHTML = `
      <span class="pip-face" aria-hidden="true">
        <i class="pip-ear pip-ear-left"></i><i class="pip-ear pip-ear-right"></i>
        <i class="pip-eye pip-eye-left"></i><i class="pip-eye pip-eye-right"></i>
      </span>
    `;
  }
  else if (kind === "system") avatarEl.textContent = "SYS";
  else avatarEl.textContent = "!";

  const contentEl = document.createElement("div");
  contentEl.className = "msg-content";

  const metaEl = document.createElement("div");
  metaEl.className = "msg-meta";
  if (kind === "user") metaEl.textContent = "You";
  else if (kind === "ai") metaEl.textContent = "Pip";
  else if (kind === "system") metaEl.textContent = "System";
  else metaEl.textContent = "Error";

  const bubbleEl = document.createElement("div");
  bubbleEl.className = "bubble";
  contentEl.appendChild(metaEl);
  contentEl.appendChild(bubbleEl);
  innerEl.appendChild(avatarEl);
  innerEl.appendChild(contentEl);
  msgEl.appendChild(innerEl);
  return { msgEl, bubbleEl, metaEl, contentEl };
}

function _normalizeTurnReferences(references = []) {
  if (!Array.isArray(references)) return [];
  return references
    .map((ref) => {
      if (!ref || typeof ref !== "object") return null;
      const messageId = String(ref.message_id || "").trim();
      const role = String(ref.role || "").trim();
      const preview = String(ref.preview || "").replace(/\s+/g, " ").trim();
      if (!messageId && !preview) return null;
      return { message_id: messageId, role, preview };
    })
    .filter(Boolean)
    .slice(0, 5);
}

function _renderInlineReferences(container, references = []) {
  const refs = _normalizeTurnReferences(references);
  container.querySelector(".msg-inline-references")?.remove();
  if (!refs.length) return;
  const wrap = document.createElement("div");
  wrap.className = "msg-inline-references";
  const visible = refs.slice(0, 2);
  for (const ref of visible) {
    const item = document.createElement("div");
    item.className = "msg-inline-reference";
    item.classList.add("ai-context-card");
    const role = ref.role ? `${ref.role}: ` : "";
    item.innerHTML = `
      <span class="msg-inline-reference-label">引用</span>
      <span class="msg-inline-reference-text">${escHtml(`${role}${ref.preview || ref.message_id}`)}</span>
    `;
    wrap.appendChild(item);
  }
  if (refs.length > visible.length) {
    const more = document.createElement("div");
    more.className = "msg-inline-reference msg-inline-reference-more";
    more.textContent = `+${refs.length - visible.length}`;
    wrap.appendChild(more);
  }
  container.prepend(wrap);
}

function _setBubbleMarkdownContent(bubbleEl, raw, options = {}, references = []) {
  bubbleEl._raw = String(raw || "");
  const refs = _normalizeTurnReferences(references);
  if (!refs.length) {
    setMarkdownContent(bubbleEl, bubbleEl._raw, options);
    return;
  }
  bubbleEl.className = Array.from(new Set(`${bubbleEl.className || ""} bubble-has-references`.trim().split(/\s+/).filter(Boolean))).join(" ");
  bubbleEl.innerHTML = "";
  _renderInlineReferences(bubbleEl, refs);
  const bodyEl = document.createElement("div");
  bodyEl.className = "msg-inline-reference-body";
  bubbleEl.appendChild(bodyEl);
  setMarkdownContent(bodyEl, bubbleEl._raw, { ...options, className: "markdown-body msg-inline-reference-body" });
}

// ── Chat message helpers ───────────────────────────────────────────────────

export function insertUserMsg(text, references = [], { clientTurnId = "", queued = false } = {}) {
  const { msgEl, bubbleEl, contentEl } = createMsgBubble("user");
  _setBubbleMarkdownContent(bubbleEl, text, { surface: "chat", className: "bubble markdown-body" }, references);
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  _bindClientTurnMessage("user", clientTurnId, msgEl);
  if (queued) _setUserMessageQueued(clientTurnId, true);
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
  state._lastUserMsgEl = msgEl;
  refreshChatStats();
  scrollToBottom();
}

function _refreshMessageActions(msgEl) {
  if (!msgEl) return;
  const bubbleEl = msgEl.querySelector(".bubble");
  const contentEl = msgEl.querySelector(".msg-content");
  if (!bubbleEl || !contentEl) return;
  contentEl.querySelector(".msg-actions-footer")?.remove();
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
}

export function applyLiveMessageIds({ userMessageId = "", assistantMessageId = "", clientTurnId = "" } = {}) {
  const turnId = String(clientTurnId || "").trim();
  const userMsgEl = (turnId && _userMsgElsByClientTurn.get(turnId)) || state._lastUserMsgEl;
  const aiMsgEl = (turnId && _aiMsgElsByClientTurn.get(turnId)) || state._aiMsgEl;
  if (userMessageId && userMsgEl) {
    userMsgEl.dataset.messageId = userMessageId;
    _setUserMessageQueued(turnId, false);
    _refreshMessageActions(userMsgEl);
  }
  if (assistantMessageId && aiMsgEl) {
    aiMsgEl.dataset.messageId = assistantMessageId;
    _refreshMessageActions(aiMsgEl);
  }
}

export function insertSystemMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("system");
  bubbleEl.textContent = text;
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
  scrollToBottom();
}

export function insertErrorMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("error");
  bubbleEl.textContent = "Error: " + text;
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
  scrollToBottom();
}

// ── Streaming AI response ──────────────────────────────────────────────────

function _ensureActiveAiBubble() {
  if (state._aiMsgEl && state._aiBubbleEl) return;
  const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
  state._aiMsgEl = msgEl;
  state._aiBubbleEl = bubbleEl;
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
}

function _isProgressOnlyBubble(bubbleEl = state._aiBubbleEl) {
  return !!(bubbleEl && bubbleEl._progressOnly && !(bubbleEl._raw || "").trim());
}

function _promoteAiBubbleToStreamingMarkdown() {
  _ensureActiveAiBubble();
  const bubbleEl = state._aiBubbleEl;
  if (!bubbleEl) return;
  bubbleEl._progressOnly = false;
  bubbleEl._streamingMarkdown = true;
  bubbleEl.classList.add("markdown-body");
}

export function showAiProgress(summary, { clientTurnId = "" } = {}) {
  const rawText = String(summary || "").trim();
  const text = _activeRoundLabel && !rawText.startsWith(_activeRoundLabel)
    ? `${_activeRoundLabel} · ${rawText}`
    : rawText;
  if (!text) return;
  if (state._thinkingEl) {
    state._thinkingStatus = text;
    _renderThinkingStatus();
    _bindClientTurnMessage("ai", clientTurnId, state._thinkingEl);
    return;
  }
  if (state._aiBubbleEl?._raw?.trim()) return;
  if (state._aiBubbleEl?._progressOnly && state._aiMsgEl) {
    state._aiMsgEl.remove();
    state._aiMsgEl = null;
    state._aiBubbleEl = null;
  }
  insertThinkingMsg(state._thinkingStart || Date.now());
  state._thinkingStatus = text;
  _renderThinkingStatus();
  _bindClientTurnMessage("ai", clientTurnId, state._thinkingEl);
}

export function appendChunk(text, { clientTurnId = "" } = {}) {
  const chunkText = String(text || "");
  _promoteAiBubbleToStreamingMarkdown();
  state._aiBubbleEl._raw = state._aiBubbleEl._raw || "";
  removeThinkingMsg();  // streaming has started — thinking indicator no longer needed
  _setAiStreamingState(true);
  _bindClientTurnMessage("ai", clientTurnId, state._aiMsgEl);
  _setUserMessageQueued(clientTurnId, false);
  state._aiBubbleEl._raw += chunkText;
  _streamBufferedChars += chunkText.length;
  _chatPerfPush("stream-chunk", {
    chunkLength: chunkText.length,
    rawLength: state._aiBubbleEl._raw.length,
  });
  _queueAiBubbleRender();
}

/**
 * Replace (not append) the current in-progress AI bubble with *text*.
 * Used during session replay to restore accumulated text without duplication.
 */
export function setChunkText(text, { clientTurnId = "" } = {}) {
  _promoteAiBubbleToStreamingMarkdown();
  _bindClientTurnMessage("ai", clientTurnId, state._aiMsgEl);
  _setUserMessageQueued(clientTurnId, false);
  state._aiBubbleEl._raw = String(text || "");
  _streamBufferedChars = Math.max(_streamBufferedChars, state._aiBubbleEl._raw.length);
  _setAiStreamingState(true);
  _queueAiBubbleRender(true);
  pinThinkingMsgToBottom();
}

export function completeAiMsgWithAuthoritativeText(text) {
  const authoritative = String(text || "");
  if (authoritative) setChunkText(authoritative);
  finalizeAiMsgNow();
}

export function finalizeAiMsg() {
  _flushAiBubbleRender();
  _finishAiMessageNow();
}

export function finalizeAiMsgNow() {
  _flushAiBubbleRender();
  _finishAiMessageNow();
  _activeRoundLabel = "";
}

export function setActiveRoundLabel(round, maxRounds = 0) {
  const n = Number(round || 0);
  if (!n) return;
  _activeRoundLabel = `R${n}${Number(maxRounds || 0) > 0 ? `/${Number(maxRounds)}` : ""}`;
}

export function discardActiveAiMsg() {
  _clearStreamTimers();
  removeThinkingMsg();
  if (state._aiMsgEl) state._aiMsgEl.remove();
  state._aiMsgEl = null;
  state._aiBubbleEl = null;
  _aiMsgElsByClientTurn.clear();
  _streamRenderQueued = false;
  _streamBufferedChars = 0;
  _streamLastRenderTs = 0;
  _lastMarkdownRenderTs = 0;
  _autoScroll = true;
  _updateJumpBtn();
}

export function hasActiveAiMessage() {
  return !!(state._aiMsgEl && state._aiBubbleEl);
}

export function hasActiveAiProgressOnly() {
  return _isProgressOnlyBubble();
}

// ── Thinking indicator ─────────────────────────────────────────────────────

export function insertThinkingMsg(startTime = Date.now()) {
  removeThinkingMsg();
  const { msgEl, bubbleEl } = createMsgBubble("ai");
  msgEl.classList.add("thinking-msg");
  const activity = createAgentActivity({
    label: "正在梳理…",
    state: AI_STATES.RUNNING,
    startedAt: startTime,
  });
  bubbleEl.replaceChildren(activity);
  bubbleEl.classList.add("thinking-bubble");
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
  scrollToBottom();
  state._thinkingEl    = msgEl;
  state._thinkingStart = startTime;
  state._thinkingStatus = "正在梳理…";
  _renderThinkingStatus();
  state._thinkingTimer = setInterval(_renderThinkingStatus, 1000);
  _chatPerfPush("thinking-start");
}

function _renderThinkingStatus() {
  if (!state._thinkingEl) return;
  const bubbleEl = state._thinkingEl.querySelector(".thinking-bubble");
  if (!bubbleEl) return;
  const sec = Math.max(0, Math.floor((Date.now() - (state._thinkingStart || Date.now())) / 1000));
  const activity = bubbleEl.querySelector(".ai-activity");
  if (activity?.updateActivity) {
    activity.updateActivity({
      label: state._thinkingStatus || "正在梳理…",
      state: AI_STATES.RUNNING,
      startedAt: state._thinkingStart || Date.now(),
    });
    return;
  }
  const copy = bubbleEl.querySelector(".thinking-copy");
  const elapsed = bubbleEl.querySelector(".thinking-elapsed");
  if (copy) copy.textContent = state._thinkingStatus || "正在梳理…";
  if (elapsed) elapsed.textContent = `${sec}s`;
}

export function removeThinkingMsg() {
  const hadThinking = !!state._thinkingEl || !!state._thinkingTimer;
  if (state._thinkingTimer) { clearInterval(state._thinkingTimer); state._thinkingTimer = null; }
  if (state._thinkingEl)    { state._thinkingEl.remove(); state._thinkingEl = null; }
  state._thinkingStatus = "";
  state._thinkingStart = 0;
  if (hadThinking) _chatPerfPush("thinking-stop");
}

export function pinThinkingMsgToBottom() {
  if (state._thinkingEl) {
    els.messages.appendChild(state._thinkingEl);
    _ensureMessagesBottomSentinel();
  }
}

export function hasVisibleInProgressMarker() {
  if (state._thinkingEl && state._thinkingEl.isConnected) return true;
  return !!els.messages.querySelector(".tool-line:not(.has-result)");
}

export function rehydrateInProgressUi(sessionId) {
  if (!isSessionRunning(sessionId)) return;
  const runtime = state._sessionRunState[sessionId] || {};
  const startedAt = runtime.startedAt || Date.now();

  // Session history is rendered through a staging host and then swapped into
  // #messages. The old thinking node can therefore remain in state while no
  // longer being connected to the visible DOM. Treat that pointer as stale;
  // otherwise showAiProgress() updates an invisible node forever.
  if (state._thinkingEl && !state._thinkingEl.isConnected) {
    if (state._thinkingTimer) clearInterval(state._thinkingTimer);
    state._thinkingTimer = null;
    state._thinkingEl = null;
    state._thinkingStatus = "";
    state._thinkingStart = startedAt;
  }

  if (!hasVisibleInProgressMarker()) {
    const activeStep = runtime.active_step || {};
    const restoredSummary = activeStep.summary || runtime.summary || "";
    if (restoredSummary) showAiProgress(restoredSummary);
    else showAiProgress("正在梳理…");
    return;
  }
  state._thinkingStart = startedAt;
  pinThinkingMsgToBottom();
  scrollToBottom();
}

// Markdown rendering is implemented in modules/markdown.js

// ── Session history restore ────────────────────────────────────────────────

function _fmtHistoryTs(rawTs) {
  const ts = Number(rawTs || 0);
  if (!Number.isFinite(ts) || ts <= 0) return "";
  return new Date(ts * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function _renderSessionSummary(summary, parent = els.messages) {
  if (!summary) return;
  const { msgEl, bubbleEl, contentEl } = createMsgBubble("system");
  msgEl.classList.add("session-history-block");
  bubbleEl.classList.add("session-history-summary");
  bubbleEl.innerHTML = `<div class="session-history-label">Compaction Summary</div><div class="session-history-markdown"></div>`;
  const summaryEl = bubbleEl.querySelector(".session-history-markdown");
  setMarkdownContent(summaryEl, summary, { surface: "chat" });
  bubbleEl._raw = summary;
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  parent.appendChild(msgEl);
  if (parent === els.messages) _ensureMessagesBottomSentinel();
}

function _renderSessionLineage(lineage, parent = els.messages) {
  if (!Array.isArray(lineage) || !lineage.length) return;
  const { msgEl, bubbleEl } = createMsgBubble("system");
  msgEl.classList.add("session-history-block");
  bubbleEl.classList.add("session-history-lineage");
  const items = lineage.map((entry) => {
    const meta = entry.meta_json || {};
    const parts = [];
    if (entry.relationship) parts.push(String(entry.relationship));
    if (meta.archived != null || meta.kept != null) {
      parts.push(`archived ${Number(meta.archived || 0)}`);
      parts.push(`kept ${Number(meta.kept || 0)}`);
    }
    return `
      <div class="session-lineage-item">
        <div class="session-lineage-title">${escHtml(parts.join(" · "))}</div>
        <div class="session-lineage-meta">${escHtml(_fmtHistoryTs(entry.ts) || "unknown time")}</div>
      </div>
    `;
  }).join("");
  bubbleEl.innerHTML = `
    <div class="session-history-label">Lineage</div>
    <div class="session-lineage-list">${items}</div>
  `;
  parent.appendChild(msgEl);
  if (parent === els.messages) _ensureMessagesBottomSentinel();
}

function _applyMessageMetadata(msgEl, metaEl, t) {
  const messageId = String(t.message_id || t.turn_id || t.source_event_id || "").trim();
  if (messageId) msgEl.dataset.messageId = messageId;
  if (t.excluded) {
    msgEl.classList.add("is-excluded");
    msgEl.dataset.excluded = "1";
    if (metaEl && !metaEl.querySelector(".msg-state-pill")) {
      const pill = document.createElement("span");
      pill.className = "msg-state-pill";
      pill.textContent = "excluded";
      metaEl.appendChild(pill);
    }
  }
}

function _renderOneTurn(t, parent = els.messages) {
  const ts = _turnDate(t);
  if (t.role === "user") {
    const { msgEl, bubbleEl, metaEl, contentEl } = createMsgBubble("user");
    _applyMessageMetadata(msgEl, metaEl, t);
    _setBubbleMarkdownContent(
      bubbleEl,
      t.content || "",
      { surface: "chat", className: "bubble markdown-body" },
      t.references || [],
    );
    addCopyActions(msgEl, bubbleEl, contentEl, ts);
    parent.appendChild(msgEl);
  } else if (t.role === "assistant") {
    const { msgEl, bubbleEl, metaEl, contentEl } = createMsgBubble("ai");
    _applyMessageMetadata(msgEl, metaEl, t);
    setMarkdownContent(bubbleEl, t.content || "", {
      surface: "chat",
      className: "bubble markdown-body",
    });
    addCopyActions(msgEl, bubbleEl, contentEl, ts);
    parent.appendChild(msgEl);
  } else if (t.role === "tool") {
    const el = document.createElement("div");
    renderToolResult(el, t.tool_name || "tool", t.content || "");
    parent.appendChild(el);
  }
  if (parent === els.messages) {
    _ensureMessagesBottomSentinel();
  }
}

export async function renderSessionHistory(session_id, turns, summary = "", lineage = []) {
  const stageHost = _ensureMessagesStage();
  const renderNonce = ++_sessionHistoryRenderNonce;
  const keepInProgress = isSessionRunning(session_id);
  const renderStart = performance.now();
  const turnList = Array.isArray(turns) ? turns : [];
  debugUiLifecycle("render_session_history", {
    session_id,
    running: keepInProgress,
    turns: turnList.length || 0,
  });
  _chatPerfPush("history-render-start", {
    targetSessionId: session_id,
    keepInProgress,
    turnCount: turnList.length || 0,
    hasSummary: !!summary,
    lineageCount: Array.isArray(lineage) ? lineage.length : 0,
  });
  if (!keepInProgress) removeThinkingMsg();
  _cancelHistoryBottomReveal();
  _clearHistoryWindow();
  els.chatArea.classList.add("session-switching");
  _clearClientTurnBindings();
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  state._lastUserMsgEl = null;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;
  resetActiveRound();
  _activeRoundLabel = "";

  setCurrentSessionId(session_id);

  if (!turnList.length && !summary && !(lineage || []).length) {
    els.chatArea.classList.remove("session-switching");
    els.messages.innerHTML = "";
    insertSystemMsg("No history for this session.");
    if (keepInProgress) rehydrateInProgressUi(session_id);
    refreshChatStats();
    return;
  }

  const { useWindowedHistory, win } = await _prepareHistoryStage(stageHost, turnList, {
    summary,
    lineage,
    keepInProgress,
  });
  if (renderNonce !== _sessionHistoryRenderNonce) return;
  await _finalizeHistoryInitialViewport(stageHost, {
    keepInProgress,
    historyWindow: win,
  });
  if (renderNonce !== _sessionHistoryRenderNonce) return;
  refreshChatStats();
  _scheduleHistoryWindowSync();
  _chatPerfPush("history-render-complete", {
    totalMs: Math.round((performance.now() - renderStart) * 10) / 10,
    turnCount: turnList.length,
    initialViewport: "latest",
    windowed: useWindowedHistory,
  });
}

// ── New session ────────────────────────────────────────────────────────────

export function newSession() {
  resetChatSessionUiState();
  insertSystemMsg("New session started. Use this when you switch to a new topic.");
}

export function resetChatSessionUiState() {
  _cancelHistoryBottomReveal();
  _clearHistoryWindow();
  removeThinkingMsg();
  _clearClientTurnBindings();
  state._pendingSessionStart = false;
  clearCurrentSessionId();
  state.inTokens   = 0;
  state.outTokens  = 0;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  resetActiveRound();
  _activeRoundLabel = "";
  els.messages.innerHTML = "";
  els.tokenStats.textContent   = "";
  document.querySelectorAll(".sidebar-session").forEach((el) => el.classList.remove("active"));
  refreshChatStats();
}

document.addEventListener("hc:durable-message-dispatched", (ev) => {
  const detail = ev instanceof CustomEvent ? ev.detail || {} : {};
  _setUserMessageQueued(detail.clientTurnId || "", false);
});

_initChatPerf();

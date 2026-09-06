/**
 * chat/tools.js — Tool labels, dev mode, tool-line bubbles, and tool-round blocks.
 *
 * Extracted from chat.js to keep individual modules under ~400 lines.
 * No imports from ../chat.js — avoids circular dependency.
 */

import { state, els, escHtml } from "../state.js";
import { setMarkdownContent } from "../markdown.js";
import { resolveFileUrl } from "../http.js";
import { markGeneratedArtifactsSeen } from "../panels/files.js";
import { AI_STATES, createProcessDisclosure } from "../ui/ai-primitives.js";

// ── Private scroll/thinking helpers (identical to chat.js, inlined to avoid circularity) ──
function _scrollToBottom() { els.messages.scrollTop = els.messages.scrollHeight; }
function _pinThinkingMsgToBottom() {
  if (state._thinkingEl) els.messages.appendChild(state._thinkingEl);
}

// ── Developer mode ─────────────────────────────────────────────────────────
// When dev mode is off (default) tool lines show friendly Chinese labels.
// When on, they show raw tool names and result previews for debugging.
export function isDevMode() {
  try { return localStorage.getItem("hushclaw.dev.mode") === "1"; } catch { return false; }
}
export function setDevMode(on) {
  try { localStorage.setItem("hushclaw.dev.mode", on ? "1" : "0"); } catch { /* ignore */ }
}

// ── Friendly tool label map ────────────────────────────────────────────────
const TOOL_LABELS = {
  // Memory
  recall:                    { icon: "💭", running: "Searching memory…",        done: "Memory retrieved",       error: "Memory search failed" },
  remember:                  { icon: "📝", running: "Saving to memory…",        done: "Saved to memory",        error: "Save failed" },
  search_notes:              { icon: "🔍", running: "Searching notes…",         done: "Notes searched",         error: "Search failed" },
  remember_skill:            { icon: "🎓", running: "Saving skill…",            done: "Skill saved",            error: "Skill save failed" },
  recall_skill:              { icon: "🎓", running: "Loading skill…",           done: "Skill loaded",           error: "Skill load failed" },
  promote_skill:             { icon: "⬆️", running: "Upgrading skill…",         done: "Skill upgraded",         error: "Skill upgrade failed" },
  // Web
  fetch_url:                 { icon: "🌐", running: "Fetching page…",           done: "Page fetched",           error: "Fetch failed" },
  // Files
  read_file:                 { icon: "📄", running: "Reading file…",            done: "File read",              error: "Read failed" },
  write_file:                { icon: "✏️", running: "Writing file…",            done: "File saved",             error: "Write failed" },
  edit_document:             { icon: "✏️", running: "Editing document…",        done: "Document edited",        error: "Edit failed" },
  update_document:           { icon: "✏️", running: "Updating document…",       done: "Document updated",       error: "Update failed" },
  patch_document:            { icon: "✂️", running: "Patching document…",       done: "Document patched",       error: "Patch failed" },
  list_dir:                  { icon: "📁", running: "Listing directory…",       done: "Directory listed",       error: "Directory error" },
  make_download_url:         { icon: "⬇️", running: "Creating download link…",  done: "Download link ready",    error: "Link creation failed" },
  make_download_bundle:      { icon: "🗂️", running: "Bundling output…",         done: "Bundle ready",           error: "Bundle failed" },
  inspect_html_artifact:     { icon: "◇", running: "Checking visual report…",  done: "Report checked",         error: "Report check failed" },
  publish_html_artifact:     { icon: "▣", running: "Publishing visual report…", done: "Visual report ready",    error: "Report publish failed" },
  // Shell
  run_shell:                 { icon: "⚡", running: "Running command…",         done: "Command complete",       error: "Command failed" },
  // System
  get_time:                  { icon: "🕐", running: "Getting time…",            done: "Time retrieved",         error: "Failed" },
  platform_info:             { icon: "💻", running: "Getting system info…",     done: "System info retrieved",  error: "Failed" },
  // Browser
  browser_navigate:          { icon: "🔗", running: "Navigating…",             done: "Page loaded",            error: "Navigation failed" },
  browser_get_content:       { icon: "📋", running: "Extracting content…",      done: "Content extracted",      error: "Extraction failed" },
  browser_click:             { icon: "👆", running: "Clicking…",               done: "Clicked",                error: "Click failed" },
  browser_fill:              { icon: "⌨️", running: "Filling form…",            done: "Form filled",            error: "Fill failed" },
  browser_submit:            { icon: "📤", running: "Submitting…",             done: "Submitted",              error: "Submit failed" },
  browser_screenshot:        { icon: "📸", running: "Taking screenshot…",       done: "Screenshot taken",       error: "Screenshot failed" },
  browser_evaluate:          { icon: "⚙️", running: "Running script…",          done: "Script complete",        error: "Script failed" },
  browser_close:             { icon: "❌", running: "Closing browser…",         done: "Closed",                 error: "Close failed" },
  browser_open_for_user:     { icon: "🪟", running: "Opening browser…",         done: "Browser opened",         error: "Open failed" },
  browser_wait_for_user:     { icon: "⏳", running: "Waiting for action…",      done: "Action complete",        error: "Timed out" },
  browser_snapshot:          { icon: "🖼️", running: "Capturing page…",          done: "Page captured",          error: "Capture failed" },
  browser_click_ref:         { icon: "👆", running: "Clicking…",               done: "Clicked",                error: "Click failed" },
  browser_fill_ref:          { icon: "⌨️", running: "Filling form…",            done: "Form filled",            error: "Fill failed" },
  browser_new_tab:           { icon: "📑", running: "Opening new tab…",         done: "Tab opened",             error: "Open failed" },
  browser_list_tabs:         { icon: "📑", running: "Listing tabs…",            done: "Tabs listed",            error: "Failed" },
  browser_focus_tab:         { icon: "📑", running: "Switching tab…",           done: "Tab switched",           error: "Switch failed" },
  browser_close_tab:         { icon: "📑", running: "Closing tab…",             done: "Tab closed",             error: "Close failed" },
  browser_connect_user_chrome: { icon: "🔌", running: "Connecting browser…",   done: "Browser connected",      error: "Connection failed" },
  // Agents
  delegate_to_agent:         { icon: "🤝", running: "Delegating to agent…",    done: "Agent responded",        error: "Delegation failed" },
  list_agents:               { icon: "🤖", running: "Loading agents…",         done: "Agents loaded",          error: "Failed" },
  broadcast_to_agents:       { icon: "📡", running: "Broadcasting…",           done: "Broadcast sent",         error: "Broadcast failed" },
  run_pipeline:              { icon: "🔄", running: "Running pipeline…",        done: "Pipeline complete",      error: "Pipeline failed" },
  create_agent:              { icon: "➕", running: "Creating agent…",         done: "Agent created",          error: "Create failed" },
  delete_agent:              { icon: "🗑️", running: "Deleting agent…",         done: "Agent deleted",          error: "Delete failed" },
  update_agent:              { icon: "✏️", running: "Updating agent…",          done: "Agent updated",          error: "Update failed" },
  spawn_agent:               { icon: "🌱", running: "Spawning sub-agent…",      done: "Sub-agent started",      error: "Spawn failed" },
  // Todos
  add_todo:                  { icon: "✅", running: "Adding todo…",             done: "Todo added",             error: "Add failed" },
  list_todos:                { icon: "📋", running: "Loading todos…",           done: "Todos loaded",           error: "Failed" },
  complete_todo:             { icon: "✅", running: "Completing todo…",         done: "Marked complete",        error: "Failed" },
  // Skills
  list_skills:               { icon: "🎒", running: "Loading skills…",         done: "Skills loaded",          error: "Failed" },
  use_skill:                 { icon: "🎓", running: "Running skill…",           done: "Skill complete",         error: "Skill failed" },
  // Email
  list_emails:               { icon: "📬", running: "Fetching emails…",         done: "Emails fetched",         error: "Fetch failed" },
  read_email:                { icon: "📧", running: "Reading email…",           done: "Email read",             error: "Read failed" },
  send_email:                { icon: "📤", running: "Sending email…",           done: "Email sent",             error: "Send failed" },
  search_emails:             { icon: "🔍", running: "Searching emails…",        done: "Emails found",           error: "Search failed" },
  mark_email_read:           { icon: "✉️", running: "Marking email…",           done: "Email marked",           error: "Mark failed" },
  move_email:                { icon: "📂", running: "Moving email…",            done: "Email moved",            error: "Move failed" },
  reply_email:               { icon: "↩️", running: "Sending reply…",           done: "Reply sent",             error: "Reply failed" },
  delete_email:              { icon: "🗑️", running: "Deleting email…",          done: "Email deleted",          error: "Delete failed" },
  forward_email:             { icon: "↪️", running: "Forwarding email…",        done: "Email forwarded",        error: "Forward failed" },
  list_email_folders:        { icon: "📁", running: "Listing folders…",         done: "Folders listed",         error: "Failed" },
};

export function _toolLabel(name) {
  return TOOL_LABELS[name] || { icon: "⚙️", running: "Processing…", done: "Done", error: "Failed" };
}

// ── Active round state ─────────────────────────────────────────────────────
let _activeRoundEl = null;
const _INLINE_ARTIFACT_EXTS = new Set([".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".mp4", ".mp3", ".webm", ".ogg", ".wav"]);

/** Reset the active round pointer (called from chat.js on session reset). */
export function resetActiveRound() { _activeRoundEl = null; }

// ── Tool-line bubbles ───────────────────────────────────────────────────────

export function insertToolBubble(_data) {
  // The primary conversation has exactly one in-progress surface. Raw tool
  // calls are recorded in the Runtime monitor by websocket.js, including in
  // developer mode, rather than inserted as a second chat row.
  _pinThinkingMsgToBottom();
  _scrollToBottom();
}

export function updateToolBubble(_data) {
  // Completion details follow the same Runtime-monitor path as call details.
  _pinThinkingMsgToBottom();
  _scrollToBottom();
}

function _normalizeArtifacts(artifacts) {
  const list = [];
  const seen = new Set();
  for (const item of Array.isArray(artifacts) ? artifacts : []) {
    const url = String(item?.preview_url || item?.entry_url || item?.url || "").trim();
    if (!url.startsWith("/files/")) continue;
    const key = String(item?.file_id || item?.artifact_id || url).trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    list.push({
      file_id: String(item?.file_id || item?.artifact_id || "").trim(),
      artifact_id: String(item?.artifact_id || item?.file_id || "").trim(),
      url,
      name: String(item?.name || url.split("/").filter(Boolean).pop() || "file").trim() || "file",
      kind: String(item?.kind || "file").trim() || "file",
      artifact_type: String(item?.artifact_type || "").trim(),
      quality: item?.quality && typeof item.quality === "object" ? item.quality : null,
    });
  }
  return list;
}

function _isInlineArtifact(name, kind = "", artifactType = "") {
  if (kind === "directory" || artifactType) return true;
  const lower = String(name || "").toLowerCase();
  const dot = lower.lastIndexOf(".");
  return dot >= 0 && _INLINE_ARTIFACT_EXTS.has(lower.slice(dot));
}

function _artifactChipHtml(artifact, index) {
  const name = escHtml(artifact.name);
  const apiKey = new URLSearchParams(location.search).get("api_key") || "";
  const href = escHtml(resolveFileUrl(artifact.url, apiKey));
  const previewable = _isInlineArtifact(artifact.name, artifact.kind, artifact.artifact_type);
  const downloadAttr = previewable ? "" : ` download="${name}"`;
  const targetAttr = previewable ? ` target="_blank" rel="noopener"` : "";
  const score = Number(artifact?.quality?.score);
  const quality = Number.isFinite(score) ? ` · QA ${Math.max(0, Math.min(100, score))}` : "";
  return `<a class="tl-artifact-chip dl-link${artifact.artifact_type ? " tl-artifact-managed" : ""}" href="${href}" data-artifact-index="${index}"${downloadAttr}${targetAttr}>${artifact.artifact_type ? "Preview · " : ""}${name}${quality}</a>`;
}

function _bindArtifactChipClicks(el, artifacts) {
  el.querySelectorAll(".tl-artifact-chip").forEach((link) => {
    link.addEventListener("click", () => {
      const index = Number(link.dataset.artifactIndex || -1);
      const artifact = Number.isInteger(index) && index >= 0 ? artifacts[index] : null;
      if (artifact) markGeneratedArtifactsSeen(artifact);
    });
  });
}

function _renderToolDetail(bodyEl, raw) {
  if (!bodyEl || bodyEl._rendered) return;
  bodyEl._toolRaw = raw;
  setMarkdownContent(bodyEl, raw, { surface: "tool", className: "tl-body" });
  bodyEl._rendered = true;
}

function _unwrapUntrustedToolResult(raw) {
  if (typeof raw !== "string" || !raw.includes("-----BEGIN UNTRUSTED CONTENT-----")) {
    return raw;
  }
  const match = raw.match(/-----BEGIN UNTRUSTED CONTENT-----\n?([\s\S]*?)\n?-----END UNTRUSTED CONTENT-----/);
  return match ? match[1].trim() : raw;
}

export function renderToolResult(el, toolName, raw, isError = false, artifacts = []) {
  const displayRaw = _unwrapUntrustedToolResult(raw);
  const hideDetail = !isError && (toolName === "use_skill" || toolName === "skill_view");
  const expandable = !hideDetail && (displayRaw.length > 100 || displayRaw.includes("\n"));
  const artifactList = _normalizeArtifacts(artifacts);
  const hasDownload = artifactList.length > 0 || /(^|[\s(])(?:https?:\/\/[^\s<)]+)?\/files\//.test(displayRaw);
  el.className     = isError ? "tool-line has-error" : "tool-line has-result";
  el.dataset.state = isError ? "error" : "done";

  const lbl  = _toolLabel(toolName);
  const text = isError ? lbl.error : lbl.done;
  const errMark = isError ? ` <span class="tl-err">✗</span>` : "";
  const visibleArtifacts = artifactList.slice(0, 2);
  const overflowCount = Math.max(0, artifactList.length - visibleArtifacts.length);
  const chipsHtml = artifactList.length
    ? `<span class="tl-artifacts">${visibleArtifacts.map((artifact, index) => _artifactChipHtml(artifact, index)).join("")}${overflowCount ? `<span class="tl-artifact-overflow">+${overflowCount}</span>` : ""}</span>`
    : "";
  const needsDetail = expandable || (hasDownload && artifactList.length === 0) || artifactList.length > visibleArtifacts.length;
  const detailHtml = needsDetail
    ? `<span class="tl-detail-btn" role="button" tabindex="0">${hasDownload && !expandable ? "· Files" : "· Details"}</span><div class="tl-body"></div>`
    : "";
  el.innerHTML = `<span class="tl-label">${lbl.icon} ${escHtml(text)}</span>`
               + errMark
               + chipsHtml
               + detailHtml;
  if (artifactList.length) _bindArtifactChipClicks(el, artifactList);
  if (needsDetail) {
    const bodyEl = el.querySelector(".tl-body");
    if (bodyEl) bodyEl._toolRaw = displayRaw;
    const btn = el.querySelector(".tl-detail-btn");
    if (btn) {
      const toggle = () => {
        _renderToolDetail(bodyEl, displayRaw);
        el.classList.toggle("expanded");
        btn.textContent = el.classList.contains("expanded")
          ? "· Collapse"
          : (hasDownload && !expandable ? "· Files" : "· Details");
      };
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggle();
      });
      btn.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          e.stopPropagation();
          toggle();
        }
      });
    }
  }

  const _roundContainer = el.closest(".tool-round");
  if (_roundContainer) _refreshRoundSummary(_roundContainer);

  if (isError) {
    const cpBtn = document.createElement("button");
    cpBtn.type = "button";
    cpBtn.className = "tl-copy-err-btn";
    cpBtn.title = "Copy error";
    cpBtn.innerHTML = `<svg width="9" height="9" viewBox="0 0 12 12" fill="none"><rect x="1.5" y="1.5" width="7" height="9" rx="1" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 4.5h5M3.5 6.5h5M3.5 8.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>`;
    cpBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(raw).then(() => {
        const origHtml = cpBtn.innerHTML;
        cpBtn.textContent = "✓";
        setTimeout(() => { cpBtn.innerHTML = origHtml; }, 1400);
      }).catch(() => {});
    });
    el.appendChild(cpBtn);
  }
}

// ── Tool round blocks ───────────────────────────────────────────────────────

function _copyRoundContent(container, btn) {
  const lines = container.querySelectorAll(".tool-line");
  const parts = [];
  for (const line of lines) {
    const label = line.querySelector(".tl-label")?.textContent?.trim() || "";
    const bodyEl = line.querySelector(".tl-body");
    const body  = bodyEl?._toolRaw || bodyEl?.textContent?.trim() || "";
    if (label) parts.push(body ? `${label}\n${body}` : label);
  }
  navigator.clipboard.writeText(parts.join("\n---\n")).then(() => {
    const orig = btn.innerHTML;
    btn.textContent = "✓";
    setTimeout(() => { btn.innerHTML = orig; }, 1400);
  }).catch(() => {});
}

function _refreshRoundSummary(roundEl) {
  const summary   = roundEl.querySelector(".tr-summary");
  const errorHint = roundEl.querySelector(".tr-error-hint");
  if (!summary) return;

  const toolLines = roundEl.querySelectorAll(".tool-line");
  let namedTools = 0;
  let firstErrorText = "";
  let allSettled = true;
  let runningCount = 0;
  let errorCount = 0;

  for (const line of toolLines) {
    const labelEl = line.querySelector(".tl-label, .tl-name");
    if (!labelEl) continue;
    namedTools += 1;
    if (line.classList.contains("has-error")) {
      errorCount += 1;
      if (!firstErrorText) {
        const bodyEl = line.querySelector(".tl-body");
        firstErrorText = bodyEl?.textContent?.trim().split("\n")[0]?.slice(0, 80) || "Failed";
      }
    }
    if (!line.classList.contains("has-result") && !line.classList.contains("has-error")) {
      allSettled = false;
      runningCount += 1;
    }
  }

  if (toolLines.length === 0) {
    summary.textContent = "Preparing actions";
  } else {
    const noun = namedTools === 1 ? "action" : "actions";
    const stateText = errorCount
      ? `${errorCount} failed`
      : allSettled
        ? "complete"
        : `${runningCount} running`;
    summary.textContent = `${namedTools} ${noun} · ${stateText}`;
  }

  roundEl.updateProcess?.({
    label: summary.textContent,
    state: errorCount
      ? AI_STATES.FAILED
      : (allSettled && toolLines.length > 0 ? AI_STATES.COMPLETED : AI_STATES.RUNNING),
  });

  if (errorHint) {
    errorHint.textContent = firstErrorText;
    errorHint.style.display = firstErrorText ? "" : "none";
  }
}

function _finalizeRound(roundEl) {
  if (!roundEl) return;
  roundEl.classList.add("collapsed");
  _refreshRoundSummary(roundEl);
}

export function finalizeActiveRound() {
  if (!_activeRoundEl) return;
  _finalizeRound(_activeRoundEl.closest(".tool-round"));
  _activeRoundEl = null;
}

export function createToolRound(_round, _maxRounds) {
  finalizeActiveRound();
  _activeRoundEl = null;
  _pinThinkingMsgToBottom();
  _scrollToBottom();
}

export function insertRoundLine(round, maxRounds) {
  const { root: wrap, body } = createProcessDisclosure({
    index: `R${round}${maxRounds > 0 ? `/${maxRounds}` : ""}`,
    label: "Processing…",
    state: AI_STATES.RUNNING,
    expanded: false,
  });

  els.messages.appendChild(wrap);
  _activeRoundEl = body;
  _scrollToBottom();
}

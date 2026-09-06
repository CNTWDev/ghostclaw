from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_harness_shell_is_the_single_final_sitewide_layer():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    density_css = (ROOT / "hushclaw" / "web" / "styles" / "density-compact.css").read_text(encoding="utf-8")
    harness_css = (ROOT / "hushclaw" / "web" / "styles" / "harness-shell.css").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/styles/density-compact.css">' in index_html
    assert '<link rel="stylesheet" href="/styles/harness-shell.css">' in index_html
    assert index_html.index("density-compact.css") < index_html.index("harness-shell.css")
    assert "ui-theme-unified.css" not in index_html
    assert "workbench-v2.css" not in index_html
    assert "gemini-shell.css" not in index_html
    assert "--threads-drawer-w: 276px;" in harness_css
    assert "--workbench-w: 360px;" in harness_css
    assert "--ui-font-body: 13px;" in density_css
    assert "--ui-weight-medium: 550;" in density_css
    assert ".msg.ai .bubble.markdown-body" in density_css
    assert ".sidebar-session-title" in density_css
    assert ".file-item-name" in density_css
    assert "body::before" in density_css
    assert "#chat-area::before" in density_css
    assert ".msg.ai.msg-streaming .bubble" in density_css
    assert ".sidebar-session.active" in density_css
    assert ".tool-round.compact-process" in density_css
    assert "round-index" in density_css


def test_session_history_navigation_lands_on_latest_without_saved_scroll_restore():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")

    assert "function _createMessagesBottomSentinel()" in chat_js
    assert "function _ensureMessagesStage()" in chat_js
    assert "const hostParent = els.messages?.parentElement;" in chat_js
    assert "hostParent.insertBefore(shell, els.messages);" in chat_js
    assert "els.chatArea.insertBefore(shell, els.messages);" not in chat_js
    assert "let _sessionHistoryRenderNonce = 0;" in chat_js
    assert 'function _alignMessagesToBottom(reason = "unknown")' in chat_js
    assert "function _alignHostToBottom(host)" in chat_js
    assert 'sentinel.scrollIntoView({ block: "end" });' not in chat_js
    assert "const _HISTORY_BOTTOM_REVEAL_IDLE_MS = 180;" in chat_js
    assert "const _HISTORY_BOTTOM_REVEAL_MAX_MS = 3000;" in chat_js
    assert "function _startHistoryBottomReveal(sessionId)" in chat_js
    assert "new MutationObserver(() => {" in chat_js
    assert "active.mutationObserver.observe(els.messages, { childList: true });" in chat_js
    assert "let _scrollStateRaf = 0;" in chat_js
    assert "function _applyMessagesScrollState()" in chat_js
    assert "requestAnimationFrame(_applyMessagesScrollState);" in chat_js
    assert 'export function noteSessionSwitchRequested(sessionId)' in chat_js
    assert 'export function noteSessionHistoryReceived(sessionId, turnCount, { summary = false, lineageCount = 0 } = {})' in chat_js
    assert "async function _prepareHistoryStage(host, turnList, { summary = \"\", lineage = [], keepInProgress = false } = {})" in chat_js
    assert "function _swapPreparedStageIntoView(stageHost, win = null)" in chat_js
    assert "async function _finalizeHistoryInitialViewport(stageHost, { keepInProgress = false, historyWindow = null } = {})" in chat_js
    assert "const stageHost = _ensureMessagesStage();" in chat_js
    assert "const renderNonce = ++_sessionHistoryRenderNonce;" in chat_js
    assert "await _prepareHistoryStage(stageHost, turnList, {" in chat_js
    assert "if (renderNonce !== _sessionHistoryRenderNonce) return;" in chat_js
    assert "await _finalizeHistoryInitialViewport(stageHost, {" in chat_js
    assert '_alignMessagesToBottom("history-swap");' in chat_js
    assert "function _alignMessagesToReplyBottom(reason = \"unknown\")" not in chat_js
    assert 'host.classList.add("no-msg-anim", "history-preparing", "history-measuring");' in chat_js
    assert 'host.classList.remove("history-measuring", "history-preparing", "no-msg-anim");' in chat_js
    assert "const shouldScrollToLatest = _historyBottomRequests.delete(session_id);" not in chat_js
    assert "const savedTop = _scrollMap.get(session_id);" not in chat_js
    assert "_cancelHistoryBottomReveal();" in chat_js
    assert "els.messages.addEventListener(\"scroll\", () => {" in chat_js
    assert "} else if (state._aiMsgEl) {" in chat_js
    assert ".messages-shell {" in chat_css
    assert ".messages-stage {" in chat_css
    assert ".messages-stage.history-preparing {" in chat_css
    assert "#chat-area.session-switching #messages {" in chat_css
    assert "#chat-area.session-swap-in #messages {" in chat_css
    assert "visibility: hidden;" in chat_css


def test_chat_perf_logging_is_removed_from_chat_runtime():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")

    assert 'console.log("[hc-chat-perf]", entry);' not in chat_js
    assert 'console.log(`[hc-chat-perf-line] ${summary}`);' not in chat_js
    assert "window.__HC_CHAT_PERF" in chat_js
    assert "_chatPerfPush(" in chat_js
    assert "function _chatPerfPush(name, data = {})" in chat_js
    assert "function _chatPerfMarkInput(name, data = {})" in chat_js
    assert "function _chatPerfPushViewport(name, data = {})" in chat_js


def test_streaming_markdown_updates_are_time_sliced_instead_of_rendering_every_chunk():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    markdown_js = (ROOT / "hushclaw" / "web" / "modules" / "markdown.js").read_text(encoding="utf-8")

    assert "const _STREAM_RENDER_MIN_MS = 32;" in chat_js
    assert "const _STREAM_RENDER_MIN_CHARS = 48;" in chat_js
    assert "let _streamRenderTimer = 0;" in chat_js
    assert "let _streamBufferedChars = 0;" in chat_js
    assert "let _streamLastRenderTs = 0;" in chat_js
    assert "function _scheduleAiBubbleRender(delayMs = 0)" in chat_js
    assert "function _flushAiBubbleRender()" in chat_js
    assert "_streamBufferedChars += chunkText.length;" in chat_js
    assert "const shouldFlushNow = force || _streamBufferedChars >= _STREAM_RENDER_MIN_CHARS || sinceLastRender >= _STREAM_RENDER_MIN_MS;" in chat_js
    assert "_queueAiBubbleRender(true);" in chat_js
    assert "_flushAiBubbleRender();" in chat_js
    assert "const api = _reactMarkdownApi();" in markdown_js


def test_streaming_markdown_is_incremental_and_final_render_only_switches_mode():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    markdown_js = (ROOT / "hushclaw" / "web" / "modules" / "markdown.js").read_text(encoding="utf-8")

    assert "bubbleEl._finalizeMarkdownScheduled = true;" in chat_js
    assert "bubbleEl._streamingMarkdown = true;" in chat_js
    assert "bubbleEl._streamingTextOnly" not in chat_js
    assert "streaming-markdown-body" not in chat_js
    assert "stream-caret" not in chat_js
    assert 'bubbleEl.classList.add("bubble-chunk-active")' in chat_js
    assert 'setMarkdownContent(bubbleEl, raw, { surface: "chat", streaming: true' in chat_js
    assert 'bubbleEl.querySelector(".react-markdown-surface")?.setAttribute("data-streaming", "false")' in chat_js
    assert "reusedStreamingTree" in chat_js
    assert '_chatPerfPush("markdown-finalize-start"' in chat_js
    assert '_chatPerfPush("markdown-finalize-complete"' in chat_js
    assert "raw: container._raw," in markdown_js
    assert "const renderRaw = preprocessMarkdownForRendering(container._raw);" in markdown_js


def test_session_history_staging_uses_final_renderer_without_native_upgrade_path():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    markdown_js = (ROOT / "hushclaw" / "web" / "modules" / "markdown.js").read_text(encoding="utf-8")

    assert 'setMarkdownContent(summaryEl, summary, { surface: "chat" });' in chat_js
    assert '{ surface: "chat", className: "bubble markdown-body" }' in chat_js
    assert "preferNative" not in chat_js
    assert "historyStatic" not in chat_js
    assert "_upgradeVisibleHistoryMarkdown" not in chat_js
    assert "_scheduleHistoryUpgradeBottomSettle" not in chat_js
    assert "const api = _reactMarkdownApi();" in markdown_js
    assert "preferNative" not in markdown_js
    assert "historyStatic" not in markdown_js


def test_large_session_history_uses_tail_first_chunking_instead_of_height_spacers():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")

    assert "const _HISTORY_WINDOW_THRESHOLD = 120;" in chat_js
    assert "const _HISTORY_REAL_TAIL_TURNS = 48;" in chat_js
    assert "const _HISTORY_PREPEND_CHUNK_SIZE = 24;" in chat_js
    assert "const _HISTORY_PREPEND_TRIGGER_PX = 220;" in chat_js
    assert "let _historyWindow = null;" in chat_js
    assert "function _scheduleHistoryWindowSync()" in chat_js
    assert "function _prependOlderHistoryChunk(win = _historyWindow)" in chat_js
    assert "function _renderTurnsRange(turns, start, end, parent = els.messages)" in chat_js
    assert 'function _mountWindowedHistory(turns, { summary = "", lineage = [], host = els.messages } = {})' in chat_js
    assert "function _syncHistoryWindow(win = _historyWindow)" in chat_js
    assert "host.scrollTop > _HISTORY_PREPEND_TRIGGER_PX" in chat_js
    assert "const splitIndex = Math.max(0, turns.length - _HISTORY_REAL_TAIL_TURNS);" in chat_js
    assert "_renderTurnsRange(turns, splitIndex, turns.length, host);" in chat_js
    assert "host.scrollTop = beforeTop + delta;" in chat_js
    assert '_chatPerfPushViewport("history-prepend-complete",' in chat_js
    assert "const useWindowedHistory = !keepInProgress && turnList.length >= _HISTORY_WINDOW_THRESHOLD;" in chat_js
    assert "const { useWindowedHistory, win } = await _prepareHistoryStage(stageHost, turnList, {" in chat_js
    assert ".history-window-block {" in chat_css
    assert ".history-window-spacer {" in chat_css
    assert "#messages.history-measuring .msg," in chat_css
    assert "content-visibility: visible;" in chat_css
    assert "contain-intrinsic-size: auto;" in chat_css
    assert "_estimateTurnHeight" not in chat_js
    assert "_hydrateHistoryTailWindow" not in chat_js
    assert "_dehydrateHistoryBlock" not in chat_js


def test_chat_scroll_styles_use_containment_for_large_histories():
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")

    assert "contain: layout;" in chat_css
    assert "overflow: visible;" in chat_css
    assert "padding: 26px 30px 0;" in chat_css
    assert "@supports (content-visibility: auto) {" in chat_css
    assert "content-visibility: auto;" in chat_css
    assert "contain-intrinsic-size: 0 180px;" in chat_css
    assert ".tool-line," in chat_css
    assert ".round-line {" in chat_css
    assert "will-change: transform, filter, box-shadow;" not in chat_css
    assert "gap: 3px;" in chat_css
    assert "transition: padding-bottom 0.14s ease;" not in chat_css
    assert "padding-bottom: 38px;" not in chat_css


def test_chat_thinking_and_tool_lines_avoid_high_frequency_idle_repaints():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    tools_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "tools.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    product_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-product.css").read_text(encoding="utf-8")
    base_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")

    assert "state._thinkingTimer = setInterval(_renderThinkingStatus, 1000);" in chat_js
    assert "thinking-elapsed" in chat_js
    assert 'msgEl.classList.add("thinking-msg");' in chat_js
    assert "thinking-dot" in base_css
    assert "setInterval(() => {" not in chat_js
    assert "The primary conversation has exactly one in-progress surface." in tools_js
    assert "function _devRuntimeSummary(base, value, label)" in websocket_js
    assert ".msg.ai.thinking-msg .msg-avatar" in product_css
    assert ".pip-face {" in product_css
    assert "setRuntimeTrace" not in tools_js
    assert "clearRuntimeTrace" not in tools_js
    assert ".runtime-trace-line {" not in base_css
    assert ".runtime-trace-label {" not in base_css
    assert ".tool-line {" in base_css
    assert "animation: tl-running 1.8s ease-in-out infinite;" not in base_css
    assert "@keyframes tl-running {" not in base_css


def test_runtime_process_feedback_uses_inline_progress_and_timing_summary():
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")

    assert "pushSessionRuntimeEvent" in websocket_js
    assert "showAiProgress" in websocket_js
    assert 'showAiProgress(runtime.summary || "正在梳理…");' in websocket_js
    assert "function _perfSummary(perf = {})" in websocket_js
    assert 'label: "Timing"' in websocket_js
    assert "export function showAiProgress(summary, { clientTurnId = \"\" } = {})" in chat_js
    assert 'showAiProgress("正在梳理…");' in chat_js
    assert 'state._thinkingEl && !state._thinkingEl.isConnected' in chat_js
    assert 'if (keepInProgress) rehydrateInProgressUi(session_id);' in chat_js
    assert "if (feed.length > 20) feed.splice(0, feed.length - 20);" in state_js


def test_message_action_footer_defers_button_mount_until_first_interaction():
    export_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "export.js").read_text(encoding="utf-8")
    base_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")

    assert "function _buildMessageActionButtons(msgEl, bubbleEl)" in export_js
    assert "function _hydrateMessageActionFooter(footer, msgEl, bubbleEl)" in export_js
    assert 'footer.dataset.actionsHydrated = "0";' in export_js
    assert 'toggleBtn.className = "msg-copy-btn msg-actions-toggle";' in export_js
    assert 'toggleBtn.innerHTML = "⋯ More";' in export_js
    assert 'footer.addEventListener("mouseenter", ensureHydrated, { once: true });' in export_js
    assert 'footer.addEventListener("focusin", ensureHydrated, { once: true });' in export_js
    assert "position: relative;" in base_css
    assert "width: 100%;" in base_css
    assert "min-height: 20px;" in base_css
    assert "margin-top: 2px;" in base_css
    assert ".msg-actions-footer > :not(.msg-time) {" in base_css
    assert "padding: 1px 6px;" in base_css
    assert "font-size: 9px;" in base_css
    assert ".msg-actions-host {" in base_css
    assert ".msg-actions-toggle[hidden] {" in base_css


def test_message_delete_requires_shared_destructive_confirmation():
    export_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "export.js").read_text(encoding="utf-8")

    assert 'import { openConfirm } from "../modal.js";' in export_js
    assert 'title: "Delete message?"' in export_js
    assert 'confirmText: "Delete message"' in export_js
    assert "dangerConfirm: true" in export_js
    assert "if (!confirmed) {" in export_js
    assert 'if (delivery === "dropped") {' in export_js
    assert export_js.index("const confirmed = await openConfirm({") < export_js.index('type: "set_message_state"')


def test_events_boot_marks_connecting_message_without_assuming_last_child():
    events_js = (ROOT / "hushclaw" / "web" / "modules" / "events.js").read_text(encoding="utf-8")

    assert 'insertSystemMsg("Connecting to HushClaw…");' in events_js
    assert 'document.querySelector("#messages .msg:last-of-type")?.setAttribute("id", "msg-connecting");' in events_js
    assert '#messages .msg:last-child' not in events_js


def test_sent_user_messages_can_render_inline_reference_summaries():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    events_js = (ROOT / "hushclaw" / "web" / "modules" / "events.js").read_text(encoding="utf-8")
    refs_js = (ROOT / "hushclaw" / "web" / "modules" / "events" / "references.js").read_text(encoding="utf-8")
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")

    assert "function _renderInlineReferences(container, references = [])" in chat_js
    assert 'item.className = "msg-inline-reference";' in chat_js
    assert 'more.className = "msg-inline-reference msg-inline-reference-more";' in chat_js
    assert "_setBubbleMarkdownContent(bubbleEl, text, { surface: \"chat\", className: \"bubble markdown-body\" }, references);" in chat_js
    assert "const referencePreviewItems = snapshotMessageReferences();" in events_js
    assert "insertUserMsg(displayText, referencePreviewItems, { clientTurnId, queued: sendResult === \"queued\" })" in events_js
    assert "export function snapshotMessageReferences()" in refs_js
    assert "z-index: 1;" in chat_css
    assert "margin: 0 0 11px;" in chat_css


def test_service_worker_caches_dynamic_modules_and_styles_for_reload_resilience():
    sw_js = (ROOT / "hushclaw" / "web" / "sw.js").read_text(encoding="utf-8")

    assert 'if (res && res.ok) {' in sw_js
    assert '!url.pathname.startsWith("/modules/")' not in sw_js


def test_ai_interaction_primitives_share_one_state_and_motion_contract():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    primitives_js = (ROOT / "hushclaw" / "web" / "modules" / "ui" / "ai-primitives.js").read_text(encoding="utf-8")
    primitives_css = (ROOT / "hushclaw" / "web" / "styles" / "ai-primitives.css").read_text(encoding="utf-8")
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    tools_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "tools.js").read_text(encoding="utf-8")
    tasks_js = (ROOT / "hushclaw" / "web" / "modules" / "tasks.js").read_text(encoding="utf-8")

    assert index_html.index("chat-product.css") < index_html.index("ai-primitives.css")
    for state in ("queued", "running", "waiting_user", "streaming", "completed", "failed", "cancelled"):
        assert state in primitives_js
    assert 'element.dataset.aiState = state;' in primitives_js
    assert 'createAgentActivity({' in chat_js
    assert 'createProcessDisclosure({' in tools_js
    assert 'applyAiState(row, taskStatus' in tasks_js
    assert "--ai-motion-instant: 120ms;" in primitives_css
    assert "--ai-motion-reveal: 380ms;" in primitives_css
    assert '@media (prefers-reduced-motion: reduce)' in primitives_css
    assert "display: block !important;" in primitives_css


def test_prompt_composer_unifies_sources_commands_and_selection_actions():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    events_js = (ROOT / "hushclaw" / "web" / "modules" / "events.js").read_text(encoding="utf-8")
    composer_js = (ROOT / "hushclaw" / "web" / "modules" / "ui" / "composer-menu.js").read_text(encoding="utf-8")
    selection_js = (ROOT / "hushclaw" / "web" / "modules" / "ui" / "selection-actions.js").read_text(encoding="utf-8")

    assert 'aria-label="Add context or action"' in index_html
    assert "initComposerMenu({" in events_js
    assert "initSelectionActions({ messages: els.messages, input: els.input });" in events_js
    assert "Upload files" in composer_js
    assert "Browse workspace files" in composer_js
    assert "Use a skill" in composer_js
    assert "Mention an agent" in composer_js
    assert '.closest?.(".msg.ai .bubble")' in selection_js
    assert "Explain" in selection_js
    assert "Improve" in selection_js
    assert "Shorten" in selection_js


def test_shared_approval_card_traps_and_restores_keyboard_focus():
    modal_js = (ROOT / "hushclaw" / "web" / "modules" / "modal.js").read_text(encoding="utf-8")
    primitives_css = (ROOT / "hushclaw" / "web" / "styles" / "ai-primitives.css").read_text(encoding="utf-8")

    assert 'cardClass: "ai-approval-card"' in modal_js
    assert "_restoreFocusTarget = document.activeElement" in modal_js
    assert 'if (ev.key === "Tab") {' in modal_js
    assert 'requestAnimationFrame(() => footerEl.querySelector("button")?.focus());' in modal_js
    assert ".app-modal-card.ai-approval-card" in primitives_css


def test_ui_lab_is_an_executable_light_and_dark_component_reference():
    lab_html = (ROOT / "hushclaw" / "web" / "ui-lab.html").read_text(encoding="utf-8")
    lab_js = (ROOT / "hushclaw" / "web" / "modules" / "ui-lab.js").read_text(encoding="utf-8")
    settings_js = (ROOT / "hushclaw" / "web" / "modules" / "settings" / "tab-system.js").read_text(encoding="utf-8")
    sw_js = (ROOT / "hushclaw" / "web" / "sw.js").read_text(encoding="utf-8")

    for primitive in ("AgentActivity", "ProcessDisclosure", "StreamingMessage", "ApprovalCard", "TaskRow", "ContextCard", "PromptComposer"):
        assert primitive in lab_html
    assert 'root.dataset.mode = dark ? "dark" : "light";' in lab_js
    assert 'href="/ui-lab.html"' in settings_js
    assert '"/ui-lab.html"' in sw_js
    assert '"/styles/ai-primitives.css"' in sw_js


def test_generated_file_badge_separates_attention_from_per_file_read_state():
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    files_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-files.css").read_text(encoding="utf-8")

    assert "const _unseenGeneratedFiles = new Map();" in files_js
    assert "const _pendingGeneratedFileAlerts = new Map();" in files_js
    assert "if (visible) _acknowledgeGeneratedArtifactAlerts();" in files_js
    assert "if (visible) _unseenGeneratedFiles.clear();" not in files_js
    assert "const unseen = _pendingGeneratedFileAlerts.size;" in files_js
    assert 'el.addEventListener("click", (ev) => {' in files_js
    assert 'el.addEventListener("dblclick", (ev) => {' not in files_js
    assert 'title="${isPreviewable ? "Click to preview" : item.name}"' in files_js
    assert 'id="files-mark-all-read"' in files_js
    assert ".files-mark-all-read" in files_css
    assert "const isReplayedArtifact = Boolean(" in websocket_js
    assert "if (!isReplayedArtifact) noteGeneratedArtifacts(data.artifacts);" in websocket_js

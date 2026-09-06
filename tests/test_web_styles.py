from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_product_motion_layer_is_opt_in_and_reduced_motion_safe():
    base_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    motion_js = (ROOT / "hushclaw" / "web" / "modules" / "motion.js").read_text(encoding="utf-8")
    app_js = (ROOT / "hushclaw" / "web" / "app.js").read_text(encoding="utf-8")

    assert "--motion-fast: 160ms;" in base_css
    assert "--motion-standard: 240ms;" in base_css
    assert ".hc-pointer-target.hc-pointer-pressed" in base_css
    assert "@media (prefers-reduced-motion: reduce)" in base_css
    assert '".session-runtime-card"' in motion_js
    assert 'document.addEventListener("pointerover"' in motion_js
    assert 'document.addEventListener("pointerdown"' in motion_js
    assert 'import "./modules/motion.js";' in app_js


def test_session_rows_expose_runtime_summary_without_new_backend_fields():
    sessions_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "sessions.js").read_text(encoding="utf-8")
    sessions_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-sessions.css").read_text(encoding="utf-8")

    assert "sidebar-session-activity" in sessions_js
    assert "runtime.active_step?.summary || runtime.summary" in sessions_js
    assert ".sidebar-session.running .sidebar-session-activity-dot" in sessions_css
    assert "session-activity-breathe" in sessions_css
    assert "prefers-reduced-motion" in sessions_css


def test_chat_markdown_long_links_wrap_inside_message_bubbles():
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")
    react_source = (ROOT / "hushclaw" / "web" / "react-src" / "react-islands.tsx").read_text(encoding="utf-8")
    markdown_native = (ROOT / "hushclaw" / "web" / "modules" / "markdown.js").read_text(encoding="utf-8")
    markdown_preprocess = (ROOT / "hushclaw" / "web" / "shared" / "markdown-preprocess.js").read_text(encoding="utf-8")

    assert ".msg-inner {\n  display: flex;\n  align-items: flex-start;\n  gap: 8px;\n  min-width: 0;" in chat_css
    assert "flex: 1 1 auto;" in chat_css
    assert "overflow-x: hidden;" in chat_css
    assert "word-break: break-word;" in chat_css
    assert ".markdown-surface :where(p, li, blockquote, td, th, a, code)" in markdown_css
    assert '.markdown-surface button[data-streamdown="link"]' in markdown_css
    assert '.markdown-surface :is(a:not(.dl-link), button[data-streamdown="link"])' in markdown_css
    assert "appearance: none;" in markdown_css
    assert "background: transparent;" in markdown_css
    assert "padding: 0;" in markdown_css
    assert "white-space: normal;" in markdown_css
    assert "overflow-wrap: anywhere;" in markdown_css
    assert '[data-md-link="compact"]' in markdown_css
    assert "md-link-modal-url" in markdown_css
    assert 'pre[data-md-diagram="true"] > code' in markdown_css
    assert 'font-variant-ligatures: none;' in markdown_css
    assert 'text-wrap: nowrap;' in markdown_css
    assert 'components={{ a: CompactMarkdownLink, pre: MarkdownPre, code: MarkdownCode }}' in react_source
    assert "compactUrlLabel" in react_source
    assert 'data-md-diagram={isDiagram ? "true" : undefined}' in react_source
    assert 'const BOX_DRAWING_GLOBAL_RE' in markdown_preprocess
    assert 'const ALIGNMENT_GAP_RE' in markdown_preprocess
    assert 'function shouldFenceAsPreformattedBlock' in markdown_preprocess
    assert 'out.push("```");' in markdown_preprocess
    assert 'const isDiagram = langNorm === "box" || isBoxDrawingCodeBlock(inner);' in markdown_native
    assert 'data-md-diagram="true"' in markdown_native


def test_markdown_math_supports_model_delimiters_and_readable_fallbacks():
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")
    vite_config = (ROOT / "vite.config.ts").read_text(encoding="utf-8")
    react_source = (ROOT / "hushclaw" / "web" / "react-src" / "react-islands.tsx").read_text(encoding="utf-8")
    markdown_native = (ROOT / "hushclaw" / "web" / "modules" / "markdown.js").read_text(encoding="utf-8")
    markdown_preprocess = (ROOT / "hushclaw" / "web" / "shared" / "markdown-preprocess.js").read_text(encoding="utf-8")
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")

    assert '"@streamdown/math": "^1.0.2"' in package_json
    assert "inlineDynamicImports: false" in vite_config
    assert 'chunkFileNames: "chunks/[name]-[hash].js"' in vite_config
    assert 'import("@streamdown/math")' in react_source
    assert 'import("katex/dist/katex.min.css")' in react_source
    assert "function containsMathSyntax(raw: string)" in react_source
    assert "function useMathPlugin(enabled: boolean)" in react_source
    assert "singleDollarTextMath: true" in react_source
    assert "plugins={mathPlugin ? { math: mathPlugin } : undefined}" in react_source
    assert "export function normalizeMathMarkdown(raw)" in markdown_preprocess
    assert "LOOSE_BLOCK_MATH_RE" in markdown_preprocess
    assert "function normalizeMathUnicodeText(value)" in markdown_preprocess
    assert 'replace(/(^|[^\\\\])%/g, "$1\\\\%")' in markdown_preprocess
    assert "Ordinary bracketed prose is left untouched." in markdown_preprocess
    assert 'class="md-math-fallback" role="math"' in markdown_native
    assert ".markdown-surface .katex-display" in markdown_css
    assert ".markdown-surface .katex-error" in markdown_css
    assert ".markdown-surface .md-math-fallback" in markdown_css


def test_chat_markdown_blocks_use_softer_line_based_surfaces():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")
    base_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    theme_css = (ROOT / "hushclaw" / "web" / "styles" / "theme-modes.css").read_text(encoding="utf-8")

    assert 'content: "";' in markdown_css
    assert "width: 26px;" in markdown_css
    assert "background: color-mix(in srgb, var(--md-section-rule) 68%, transparent);" in markdown_css
    assert "background: color-mix(in srgb, var(--md-callout-bg) 12%, transparent);" in markdown_css
    assert "background: color-mix(in srgb, var(--surface2) 9%, transparent);" in markdown_css
    assert "background: var(--md-table-head-bg);" in markdown_css
    assert "background: var(--md-code-bg);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--md-accent) 14%, transparent);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--md-table-border) 42%, transparent);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--md-code-border) 42%, transparent);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--border) 50%, transparent);" in base_css
    assert "border-bottom: 1px solid color-mix(in srgb, var(--border2) 34%, transparent);" in base_css
    assert "--md-section-rule: color-mix(in srgb, var(--md-h2-to) 22%, transparent);" in theme_css
    assert "--md-section-rule: color-mix(in srgb, var(--md-h2-to) 24%, transparent);" in theme_css


def test_chat_markdown_headings_use_single_rule_hierarchy():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")
    density_css = (ROOT / "hushclaw" / "web" / "styles" / "density-compact.css").read_text(encoding="utf-8")

    assert "border-bottom: 0;" in markdown_css
    assert ".markdown-surface-rich h1::after" in markdown_css
    assert "height: 1px;" in markdown_css
    assert ".markdown-surface-rich h2::after" not in markdown_css
    assert ".markdown-surface-rich h3::after" not in markdown_css
    assert ':root[data-theme="vector"] .markdown-surface-rich h1::after' in markdown_css
    assert ':root[data-theme="vector"] .markdown-surface-rich h2::after' not in markdown_css
    assert "--ui-font-content-h1: 18px;" in density_css
    assert "--ui-font-content-h2: 15.5px;" in density_css
    assert "--ui-font-content-h3: 14px;" in density_css
    assert "--ui-font-content-h4: 13px;" in density_css
    assert "--md-h1-size: var(--ui-font-content-h1, 18px);" in markdown_css
    assert "--md-h2-size: var(--ui-font-content-h2, 15.5px);" in markdown_css
    assert "--md-h3-size: var(--ui-font-content-h3, 14px);" in markdown_css
    assert "--md-h4-size: var(--ui-font-content-h4, 13px);" in markdown_css


def test_chat_markdown_hr_is_weaker_and_avoids_heading_double_rules():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")

    assert "background: color-mix(in srgb, var(--md-section-rule) 30%, transparent);" in markdown_css
    assert "opacity: 0.34;" in markdown_css
    assert ".markdown-surface :where(h1, h2) + hr," in markdown_css
    assert ".markdown-surface hr + :where(h1, h2) {" in markdown_css


def test_chat_markdown_longform_reading_density_is_tighter():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")
    density_css = (ROOT / "hushclaw" / "web" / "styles" / "density-compact.css").read_text(encoding="utf-8")

    assert "--ui-font-body: 13px;" in density_css
    assert "--md-body-size: var(--ui-font-body, 13px);" in markdown_css
    assert "--md-body-leading: 1.62;" in markdown_css
    assert "--md-list-leading: 1.56;" in markdown_css
    assert "--md-gap-md: 11px;" in markdown_css
    assert "--md-measure: 74ch;" in markdown_css
    assert "max-width: min(100%, calc(var(--md-measure) + 4ch));" in markdown_css
    assert "color: color-mix(in srgb, var(--md-accent) 44%, var(--text));" in markdown_css
    assert "margin-top: calc(var(--md-gap-sm) - 1px);" in markdown_css
    assert "margin-bottom: calc(var(--md-gap-sm) - 1px);" in markdown_css


def test_chat_markdown_inline_code_and_tables_are_quieter_for_longform_reading():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")

    assert "padding: 1px 4px;" in markdown_css
    assert "border: 1px solid color-mix(in srgb, var(--md-inline-code-border) 74%, var(--border));" in markdown_css
    assert "background: color-mix(in srgb, var(--md-inline-code-bg) 70%, var(--surface2) 30%);" in markdown_css
    assert "color: color-mix(in srgb, var(--md-inline-code-color) 72%, var(--text));" in markdown_css
    assert "padding: 7px 10px;" in markdown_css
    assert "font: 740 11px/1.45 var(--sans);" in markdown_css
    assert "background: color-mix(in srgb, var(--md-table-row-alt) 78%, transparent);" in markdown_css


def test_share_image_export_normalizes_oklch_before_html2canvas():
    export_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "export.js").read_text(encoding="utf-8")

    assert "H2C_UNSUPPORTED_COLOR_RE" in export_js
    assert "function _oklchToRgba(" in export_js
    assert "function _linearSrgbToByte(" in export_js
    assert "_hasUnsupportedColorFn(val)" in export_js
    assert "card.style.setProperty(token, _fixColorFn(value));" in export_js
    assert "[clonedDoc.documentElement, clonedDoc.body].filter(Boolean)" in export_js


def test_chat_product_surface_is_loaded_after_the_shell_contract():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-product.css").read_text(encoding="utf-8")
    sw_js = (ROOT / "hushclaw" / "web" / "sw.js").read_text(encoding="utf-8")

    assert '/styles/chat-product.css' in index_html
    assert index_html.index('/styles/harness-shell.css') < index_html.index('/styles/chat-product.css')
    assert '"/styles/chat-product.css"' in sw_js
    assert "max-width: none;" in chat_css
    assert "width: min(100%, var(--chat-reading-width));" in chat_css
    assert "#chat-composer .input-wrap" in chat_css
    assert ".chat-stats-strip:has(.chat-session-title) .chat-assistant-identity" in chat_css
    assert ':root[data-theme="vector"][data-mode] .msg.ai .bubble' in chat_css


def test_ui_foundations_match_the_measured_product_control_scale():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    ui_css = (ROOT / "hushclaw" / "web" / "styles" / "ui-foundations.css").read_text(encoding="utf-8")
    sw_js = (ROOT / "hushclaw" / "web" / "sw.js").read_text(encoding="utf-8")

    assert '/styles/ui-foundations.css' in index_html
    assert index_html.index('/styles/harness-shell.css') < index_html.index('/styles/ui-foundations.css')
    assert index_html.index('/styles/ui-foundations.css') < index_html.index('/styles/chat-product.css')
    assert '"/styles/ui-foundations.css"' in sw_js
    assert 'const CACHE = "hushclaw-v31";' in sw_js
    assert '--sans: Inter, "Inter Fallback", ui-sans-serif' in ui_css
    assert '--ui-type-body: 14px;' in ui_css
    assert '--ui-type-control: 12.5px;' in ui_css
    assert '--ui-type-input: 13px;' in ui_css
    assert '--ui-control-height: 33px;' in ui_css
    assert '--ui-control-radius: 9px;' in ui_css
    assert '.agents-new-btn,' in ui_css
    assert '.agent-filter-btn.active,' in ui_css
    assert '.skill-card-actions button,' in ui_css
    assert 'The chat owns its geometry, but not a different font language.' in ui_css
    assert ".tab.active {\n  background: color-mix(in srgb, var(--surface2) 72%, var(--surface));\n  border-color:" in ui_css
    assert ".tab.active::before {\n  left: -1px;" in ui_css
    assert "box-shadow: inset 2px 0 0 var(--accent);" not in ui_css
    assert ".app-rail-toggle,\n.tab," not in ui_css

    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-product.css").read_text(encoding="utf-8")
    assert ".composer-recommendation {\n  height: 28px;" in chat_css
    assert "font-size: 11.5px;\n  font-weight: 500;" in chat_css
    assert "font-size: 13px;\n  line-height: 1.55;" in chat_css


def test_product_icons_share_one_blue_vector_source_and_high_resolution_assets():
    icon_svg = (ROOT / "hushclaw" / "web" / "icon.svg").read_text(encoding="utf-8")
    manifest = (ROOT / "hushclaw" / "web" / "manifest.json").read_text(encoding="utf-8")
    sw_js = (ROOT / "hushclaw" / "web" / "sw.js").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build_icons_macos.sh").read_text(encoding="utf-8")

    assert 'id="app-bg"' in icon_svg
    assert 'stop-color="#79B8FF"' in icon_svg
    assert 'stop-color="#235DD9"' in icon_svg
    assert 'id="mark-fill"' in icon_svg
    assert '<rect x="64" y="64" width="896" height="896" rx="200"' in icon_svg
    assert '"src": "/favicon-512.png"' in manifest
    assert '"sizes": "512x512"' in manifest
    assert '"/favicon-512.png"' in sw_js
    assert 'SOURCE_SVG="$ROOT_DIR/hushclaw/web/icon.svg"' in build_script
    assert 'iconutil -c icns' in build_script
    assert (ROOT / "hushclaw" / "web" / "favicon-512.png").is_file()
    assert (ROOT / "assets" / "macos" / "HushClaw.icns").is_file()


def test_share_card_uses_one_product_design_without_template_picker():
    export_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "export.js").read_text(encoding="utf-8")
    share_css = (ROOT / "hushclaw" / "web" / "styles" / "share-card.css").read_text(encoding="utf-8")

    assert 'card.dataset.design   = "hushclaw-unified";' in export_js
    assert "_buildTemplatePickerHtml" not in export_js
    assert "Choose share style" not in export_js
    assert "_copyUnifiedShareImage(bubbleEl, imgBtn);" in export_js
    assert 'datetime.split(" ")[0] || datetime' not in export_js
    assert 'const fDatetime = _mk("span", "cimg-footer-datetime", datetime);' not in export_js
    assert "fRightInner.appendChild(fBrand);" in export_js
    assert '.cimg-card[data-design="hushclaw-unified"]' in share_css
    assert ".cimg-footer-brand {" in share_css
    assert ".cimg-footer-datetime {" not in share_css


def test_share_card_uses_quiet_layered_surface_without_decorative_wash():
    share_css = (ROOT / "hushclaw" / "web" / "styles" / "share-card.css").read_text(encoding="utf-8")

    assert "background: color-mix(in srgb, var(--ci-bg) 34%, var(--ci-bg-soft));" in share_css
    assert ".cimg-card::before {\n  content: \"\";\n  display: none;" in share_css
    assert "linear-gradient(180deg, rgba(255, 255, 255, 0.028), transparent 24%)" not in share_css
    assert "linear-gradient(180deg, rgba(255, 255, 255, 0.07), transparent 28%)" not in share_css
    assert "linear-gradient(180deg, rgba(255, 255, 255, 0.025), transparent 24%)" not in share_css


def test_product_theme_is_fixed_and_legacy_choice_is_migrated():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    theme_js = (ROOT / "hushclaw" / "web" / "modules" / "theme.js").read_text(encoding="utf-8")
    system_js = (ROOT / "hushclaw" / "web" / "modules" / "settings" / "tab-system.js").read_text(encoding="utf-8")
    theme_css = (ROOT / "hushclaw" / "web" / "styles" / "theme-modes.css").read_text(encoding="utf-8")

    assert 'localStorage.removeItem("hushclaw.ui.theme");' in index_html
    assert 'r.dataset.theme = "vector";' in index_html
    assert 'export const THEMES = ["vector"];' in theme_js
    assert 'localStorage.removeItem(THEME_STORAGE_KEY);' in theme_js
    assert "data-theme-pick" not in system_js
    assert "The HushClaw design system is fixed." in system_js
    assert "HUSHCLAW UNIFIED DESIGN SYSTEM" in theme_css
    assert "--accent: oklch(68% .173 253.301);" in theme_css


def test_product_pages_use_one_sitewide_component_contract():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    pages_css = (ROOT / "hushclaw" / "web" / "styles" / "product-pages.css").read_text(encoding="utf-8")
    sw_js = (ROOT / "hushclaw" / "web" / "sw.js").read_text(encoding="utf-8")

    assert '/styles/product-pages.css' in index_html
    assert index_html.index('/styles/product-pages.css') < index_html.index('/styles/harness-shell.css')
    assert '"/styles/product-pages.css"' in sw_js
    for selector in (
        ".agents-toolbar,",
        ".skills-toolbar,",
        ".app-connectors-header,",
        ".mem-panel-header-top,",
        ".logs-toolbar,",
        "#panel-calendar .cal-toolbar",
        ".tasks-section",
        ".wizard-card,",
        ".file-preview-dialog,",
    ):
        assert selector in pages_css
    assert "no page-specific palette" in pages_css


def test_agent_and_skill_pages_keep_content_measurable_for_vertical_scrolling():
    pages_css = (ROOT / "hushclaw" / "web" / "styles" / "product-pages.css").read_text(encoding="utf-8")

    scroll_contract = """#panel-agents #agents-list > .agents-workbench,
#panel-agents .agents-workbench > .agents-table,
#panel-skills .skills-content > * {
  flex-shrink: 0;
}"""
    assert scroll_contract in pages_css
    assert "the parent can measure the full document" in pages_css


def test_markdown_system_is_shared_by_chat_file_share_print_forum_and_tools():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    markdown_js = (ROOT / "hushclaw" / "web" / "modules" / "markdown.js").read_text(encoding="utf-8")
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-system.css").read_text(encoding="utf-8")
    react_source = (ROOT / "hushclaw" / "web" / "react-src" / "react-islands.tsx").read_text(encoding="utf-8")
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    export_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "export.js").read_text(encoding="utf-8")
    forum_js = (ROOT / "hushclaw" / "web" / "transsion" / "forum.js").read_text(encoding="utf-8")
    tools_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "tools.js").read_text(encoding="utf-8")

    assert '/styles/markdown-system.css' in index_html
    assert "export const MARKDOWN_SURFACES = Object.freeze({" in markdown_js
    for surface in ("chat", "file", "share", "print", "forum", "tool"):
        assert f"{surface}:" in markdown_js
        assert f".markdown-surface-{surface}" in markdown_css
    assert 'className="markdown-renderer react-markdown-surface"' in react_source
    assert "markdown-surface-${safeSurface}" not in react_source
    assert 'setMarkdownContent(previewEl, text, { surface: "file"' in files_js
    assert 'const printSurface = markdownSurfaceClass("print");' in export_js
    assert 'href="styles/markdown-system.css"' in export_js
    assert 'surface: "forum"' in forum_js
    assert 'surface: "tool"' in tools_js


def test_connections_panel_unifies_apps_channels_and_sync_sources():
    panel_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "app_connectors.js").read_text(encoding="utf-8")
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    panel_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-app-connectors.css").read_text(encoding="utf-8")

    assert 'const CONNECTION_KIND_ORDER = ["app", "channel", "sync_source"];' in panel_js
    assert 'const CONNECTION_KIND_LABELS = {' in panel_js
    assert 'Manage apps, channels, and sync sources from one directory.' in panel_js
    assert '_renderConnectionDetailsModal(item)' in panel_js
    assert 'const CHANNEL_PROVIDER_IDS = new Set(CHANNELS.map((channel) => channel.id));' in panel_js
    assert 'function _isChannelConnection(item) {' in panel_js
    assert '_renderChannelConfigModal(item)' in panel_js
    assert '_saveChannelConfig(provider)' in panel_js
    assert 'Reply protocol' in panel_js
    assert 'name: "iMessage"' in panel_js
    assert 'name: "WhatsApp"' not in panel_js
    assert 'provider="whatsapp"' in (ROOT / "hushclaw" / "connections" / "view.py").read_text(encoding="utf-8")
    assert 'app-connector-meta-chip' in panel_js
    assert 'wizard.tab = "integrations";' in panel_js
    assert 'No Settings or Wizard hand-off is required.' in panel_js
    assert 'title: `${isAppPanel || _isChannelConnection(item) ? "Configure" : "View"} ${item.name}`' in panel_js
    assert 'Open Integrations' in panel_js
    assert '<span>Connections</span>' in index_html
    assert 'data-desc="Manage apps, channels, and sync sources"' in index_html
    assert '.app-connector-kind-chip {' in panel_css
    assert '.app-connector-card-telegram {' in panel_css
    assert '.app-connector-card-email {' in panel_css
    assert '.app-connector-card-whatsapp {' in panel_css
    assert '.app-connector-meta-chip {' in panel_css
    assert 'function _kindEmoji(kind) {' in panel_js
    assert 'class="app-connector-card-head"' in panel_js
    assert 'class="app-connector-card-body"' in panel_js
    assert 'class="app-connector-provider-row"' in panel_js
    assert '.app-connector-card-head {' in panel_css
    assert '.app-connector-card-body {' in panel_css
    assert '.app-connector-provider-row {' in panel_css
    assert 'height: auto;' in panel_css
    assert 'text-transform: none;' in panel_css
    assert 'white-space: normal;' in panel_css


def test_channel_forms_expose_reply_protocol_instead_of_markdown_toggle():
    providers_js = (ROOT / "hushclaw" / "web" / "modules" / "settings" / "providers.js").read_text(encoding="utf-8")

    assert "Reply protocol" in providers_js
    assert 'id="tg-render-mode"' in providers_js
    assert 'id="fs-render-mode"' in providers_js
    assert 'id="dc-render-mode"' in providers_js
    assert 'id="sl-render-mode"' in providers_js
    assert 'id="dt-render-mode"' in providers_js
    assert 'id="wc-render-mode"' in providers_js
    assert 'id="wa-render-mode"' in providers_js
    assert 'id="wa-account-sid"' in providers_js
    assert 'id="wa-auth-token"' in providers_js
    assert 'id="wa-from-number"' in providers_js
    assert "Markdown replies" not in providers_js


def test_settings_wizard_no_longer_exposes_channels_tab():
    settings_js = (ROOT / "hushclaw" / "web" / "modules" / "settings" / "tab-misc.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")

    assert '{ id: "channels",     label: t("stab_channels") }' not in settings_js
    assert 'case "channels":     renderChannelsTab();' not in settings_js
    assert "export function renderChannelsTab()" not in settings_js
    assert "export function updateChannelStatusDots()" not in settings_js
    assert 'wizard.tab === "channels"' not in websocket_js
    assert 'updateChannelStatusDots' not in websocket_js


def test_skills_panel_uses_inspect_then_install_flow():
    skills_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "skills.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    skills_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-skills.css").read_text(encoding="utf-8")

    assert 'export function handleSkillSourceInspected(data)' in skills_js
    assert 'send({ type: "inspect_skill_source", source: url });' in skills_js
    assert 'type: "install_skill_source",' in skills_js
    assert "Add External Skill" in skills_js
    assert "User Global" in skills_js
    assert "Multiple skill candidates found. Pick one before installing." in skills_js
    assert 'case "skill_source_inspected":' in websocket_js
    assert ".skill-source-preview {" in skills_css
    assert ".skill-source-candidate {" in skills_css


def test_skills_panel_exposes_override_governance_actions():
    skills_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "skills.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    skills_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-skills.css").read_text(encoding="utf-8")

    assert "Governance" in skills_js
    assert 'send({ type: "prune_skill_overrides", name: skillName });' in skills_js
    assert 'case "skill_overrides_pruned":' in websocket_js
    assert ".skill-governance-summary {" in skills_css
    assert ".skill-chain-action.ok {" in skills_css


def test_skills_live_search_preserves_focus_and_selection():
    skills_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "skills.js").read_text(encoding="utf-8")

    assert 'function _captureSkillsPanelUiState() {' in skills_js
    assert 'document.activeElement !== searchInput' in skills_js
    assert 'focusId: "skills-search-input"' in skills_js
    assert 'function _restoreSkillsPanelUiState(snapshot) {' in skills_js
    assert 'input.focus({ preventScroll: true });' in skills_js
    assert "input.setSelectionRange(start, end);" in skills_js
    assert "const uiState = _captureSkillsPanelUiState();" in skills_js
    assert "_restoreSkillsPanelUiState(uiState);" in skills_js


def test_runtime_amendments_leave_chat_composer_interactive():
    events_js = (ROOT / "hushclaw" / "web" / "modules" / "events.js").read_text(encoding="utf-8")
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")

    assert 'insertSystemMsg("This session is still running. Stop it, wait for it to finish, or start a new session to send another message.");' not in events_js
    assert "const sendingIntoRunningSession = Boolean(currentSessionId && isSessionRunning(currentSessionId));" in events_js
    assert "if (!sendingIntoRunningSession) setSending(true);" in events_js
    assert "const locked = pendingStart;" in state_js
    assert "els.btnSend.disabled = locked;" in state_js
    assert "els.input.disabled = locked;" in state_js
    assert "const busy = currentRunning || pendingStart;" not in state_js


def test_runtime_workbench_promotes_monitor_and_child_runs():
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    style_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")

    assert 'chatWorkspace:     $("chat-workspace"),' in state_js
    assert 'chatWorkbench:     $("chat-workbench"),' in state_js
    assert 'runtimeMonitor:    $("runtime-monitor"),' in state_js
    assert 'sessionRuntimeMeta: $("session-runtime-meta"),' in state_js
    assert 'sessionRuntimeStack: $("session-runtime-stack"),' in state_js
    assert "export function noteSessionChildRun(sessionId, childRun = {}) {" in state_js
    assert "function _activeRuntimeChildRuns(runtime = {}) {" in state_js
    assert "function _runtimeDisplay(runtime = {}) {" in state_js
    assert 'function _renderRuntimeStack(runtime = {}) {' in state_js
    assert 'function _syncWorkbenchVisibility(runtimeVisible) {' in state_js
    assert 'els.runtimeMonitor.classList.toggle("hidden", !visible);' in state_js
    assert 'case "child_run_state_changed":' in websocket_js
    assert "noteSessionChildRun(eventSessionId(data) || getCurrentSessionId()," in websocket_js
    assert 'scope: "child",' in websocket_js
    assert '.chat-workspace {' in style_css
    assert '.chat-workbench {' in style_css
    assert '.runtime-monitor {' in style_css
    assert '.session-runtime-meta {' in style_css
    assert '.session-runtime-stack {' in style_css
    assert '.session-runtime-card {' in style_css
    assert '.workbench-preview {' in style_css
    assert 'id="runtime-monitor"' in index_html
    assert 'id="session-runtime-meta"' in index_html
    assert 'id="session-runtime-stack"' in index_html


def test_workbench_preview_and_session_drafts_are_integrated():
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    events_js = (ROOT / "hushclaw" / "web" / "modules" / "events.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    style_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")

    assert 'export function closeWorkbenchPreview()' in files_js
    assert 'document.getElementById("workbench-preview-close")?.addEventListener("click", closeWorkbenchPreview);' in files_js
    assert 'openDialog({' in files_js
    assert 'cardClass: "app-modal-card--document"' in files_js
    assert "function _openModalPreview(item, html," in files_js
    assert 'refreshWorkbenchVisibility();' in files_js
    assert 'pushWorkbenchActivity({' in files_js
    assert '_openPreviewByItem(firstPreviewable);' not in files_js
    assert 'const _COMPOSER_DRAFTS_KEY = "hushclaw.ui.composer-drafts";' in state_js
    assert 'export function saveCurrentComposerDraft() {' in state_js
    assert 'export function restoreComposerDraft(sessionId) {' in state_js
    assert 'export function clearComposerDraft(sessionId) {' in state_js
    assert 'export function pushWorkbenchActivity(item = {}) {' in state_js
    assert 'export function renderWorkbenchActivity() {' in state_js
    assert 'if (els.input && prevSid !== sid) restoreComposerDraft(sid);' in state_js
    assert 'saveCurrentComposerDraft();' in events_js
    assert 'clearComposerDraft(currentSessionId);' in events_js
    assert 'pushWorkbenchActivity({' in websocket_js
    assert 'id="workbench-activity"' in index_html
    assert ".workbench-activity {" in style_css
    assert ".workbench-activity-item {" in style_css


def test_workbench_activity_and_child_run_cards_are_actionable():
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    events_js = (ROOT / "hushclaw" / "web" / "modules" / "events.js").read_text(encoding="utf-8")
    style_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")

    assert "actionType: String(item.actionType || \"\").trim()," in state_js
    assert "artifactUrl: String(item.artifactUrl || \"\").trim()," in state_js
    assert 'class="session-runtime-card-toggle"' in state_js
    assert 'document.dispatchEvent(new CustomEvent("hc:workbench-activity-action"' in state_js
    assert 'detail.actionType !== "preview_artifact"' in files_js
    assert 'actionType: "preview_artifact",' in files_js
    assert 'actionType: "open_session",' in websocket_js
    assert 'detail.actionType !== "open_session"' in events_js
    assert ".session-runtime-card-toggle {" in style_css
    assert ".session-runtime-card-detail {" in style_css
    assert ".workbench-activity-item.actionable {" in style_css


def test_workbench_activity_is_grouped_and_preview_is_session_pinned():
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")
    style_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")

    assert 'const _WORKBENCH_PREVIEW_KEY = "hushclaw.ui.workbench-preview";' in state_js
    assert "function _loadWorkbenchPreviewState() {" in state_js
    assert "export function getWorkbenchPreviewState(sessionId) {" in state_js
    assert "export function setWorkbenchPreviewState(sessionId, previewState) {" in state_js
    assert 'document.dispatchEvent(new CustomEvent("hc:session-context-changed"' in state_js
    assert "function _groupWorkbenchActivityItems(items = []) {" in state_js
    assert "Needs Attention" in state_js
    assert "Recent Results" in state_js
    assert "Background Updates" in state_js
    assert 'id="workbench-preview-pin"' in index_html


def test_runtime_monitor_defaults_to_expanded_log_and_files_lead_workbench():
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    style_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    files_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-files.css").read_text(encoding="utf-8")
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")

    assert 'btnToggleRuntimeInline: $("btn-toggle-runtime-inline"),' in state_js
    assert 'state._runtimeMonitorHidden = snapshot.monitorHidden == null' in state_js
    assert "monitorHidden: Boolean(state._runtimeMonitorHidden)," in state_js
    assert 'const _WORKBENCH_PANELS_KEY = "hushclaw.ui.workbench-panels";' in state_js
    assert "function _loadWorkbenchPanelPrefs() {" in state_js
    assert "export function isWorkbenchPanelPreferredVisible(panel) {" in state_js
    assert "export function isWorkbenchPanelVisible(panel, { runtime = null, feed = [] } = {}) {" in state_js
    assert "export function setWorkbenchPanelVisible(panel, visible, { runtime = null, feed = [] } = {}) {" in state_js
    assert "export function toggleWorkbenchPanel(panel, { runtime = null, feed = [] } = {}) {" in state_js
    assert "function _syncRuntimeMonitorButtons(hasContent, visible) {" in state_js
    assert "_sessionRuntimeLogOpen: true," in state_js
    assert "state._sessionRuntimeLogOpen = snapshot.logOpen !== false;" in state_js
    assert "function _scrollRuntimeLogToLatest() {" in state_js
    assert "els.sessionRuntimeLog.scrollTop = els.sessionRuntimeLog.scrollHeight;" in state_js
    assert 'id="btn-toggle-runtime-inline"' in index_html
    assert 'id="btn-toggle-activity-inline"' in index_html
    assert 'id="session-runtime-hide"' not in index_html
    assert index_html.index('id="files-sidebar"') < index_html.index('id="runtime-monitor"')
    assert 'class="workbench-card workbench-section workbench-files hidden"' in index_html
    assert 'class="workbench-card workbench-section runtime-monitor hidden"' in index_html
    assert '<div class="workbench-preview-kicker">Runtime</div>' in index_html
    assert '<div class="workbench-section-title">Execution monitor</div>' in index_html
    assert 'aria-expanded="true" aria-controls="session-runtime-log">Collapse</button>' in index_html
    assert 'setWorkbenchPanelVisible("files", legacy !== "true");' in files_js
    assert 'toggleWorkbenchPanel("files");' in files_js
    assert 'const preferredVisible = isWorkbenchPanelPreferredVisible("files");' in files_js
    assert '.sessionRuntimeToggle.textContent = state._sessionRuntimeLogOpen ? "Collapse" : "Expand";' in state_js
    assert ".files-search-bar {" in files_css
    assert ".file-item {" in files_css
    assert "background: transparent;" in files_css
    assert 'document.getElementById("workbench-preview-pin")?.addEventListener("click"' in files_js
    assert 'document.addEventListener("hc:session-context-changed"' in files_js
    assert "function _syncWorkbenchPreviewHeader() {" in files_js
    assert "function _persistWorkbenchPreview() {" in files_js
    assert "function _restoreWorkbenchPreviewForSession(sessionId) {" in files_js
    assert 'group: "results",' in files_js
    assert ".workbench-preview-pin" in style_css
    assert ".workbench-activity-group {" in style_css
    assert ".workbench-activity-group-head {" in style_css
    assert ".chat-workbench {" in style_css
    assert ".workbench-section-head {" in style_css
    assert ".workbench-section-title {" in style_css
    assert ".workbench-section + .workbench-section::before {" not in style_css
    assert ".session-runtime-log {" in style_css
    assert "max-height: min(24vh, 220px);" in style_css
    assert "overflow: auto;" in style_css
    assert "box-shadow: 0 16px 30px rgba(0,0,0,0.08);" in style_css


def test_files_use_modal_preview_and_do_not_restore_session_preview_automatically():
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    files_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-files.css").read_text(encoding="utf-8")

    assert 'import { openConfirm, openDialog, closeModal } from "../modal.js";' in files_js
    assert 'function _openModalPreview(item, html,' in files_js
    assert 'label: "Download",' in files_js
    assert 'label: "Close",' in files_js
    assert 'closeModal()' in files_js
    assert "File and artifact previews are modal-first now; do not auto-open" in files_js
    assert '_openPreviewByItem(snapshot.item)' not in files_js
    assert ".file-preview-dialog {" in files_css
    assert ".file-preview-dialog-meta {" in files_css
    assert ".file-preview-dialog-body {" in files_css


def test_share_image_export_preset_adapts_min_height_to_short_content():
    export_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "export.js").read_text(encoding="utf-8")

    assert "const lineCount = Math.max(1, text.split(/\\n+/).filter(Boolean).length);" in export_js
    assert "const compact = text.length > 2200 || lineCount > 28;" in export_js
    assert "if (text.length < 420 && lineCount <= 6) {" in export_js
    assert "minHeight = 760;" in export_js
    assert "} else if (text.length < 900 && lineCount <= 11) {" in export_js
    assert "minHeight = 860;" in export_js
    assert "} else if (text.length < 1500 && lineCount <= 18) {" in export_js
    assert "minHeight = 980;" in export_js


def test_workbench_activity_is_manageable_and_runtime_cards_can_focus():
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    style_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")

    assert "read: Boolean(item.read)," in state_js
    assert "export function markWorkbenchActivityRead(id) {" in state_js
    assert "export function dismissWorkbenchActivity(id) {" in state_js
    assert 'data-dismiss-activity="${escHtml(item.id)}"' in state_js
    assert 'state._runtimeFocusedRunId = state._runtimeFocusedRunId === runId ? "" : runId;' in state_js
    assert 'const visibleFeed = focusedRun' in state_js
    assert 'session-runtime-card${expanded ? " expanded" : ""}${focused ? " focused" : ""}' in state_js
    assert ".session-runtime-card.focused {" in style_css
    assert ".workbench-activity-item.unread {" in style_css
    assert ".workbench-activity-dismiss {" in style_css


def test_workbench_attention_strip_and_runtime_ui_state_are_persistent():
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    style_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")

    assert 'const _WORKBENCH_RUNTIME_UI_KEY = "hushclaw.ui.workbench-runtime";' in state_js
    assert "function _loadWorkbenchRuntimeUiState() {" in state_js
    assert "function _loadRuntimeUiForSession(sessionId) {" in state_js
    assert "function _persistRuntimeUiForSession(sessionId) {" in state_js
    assert "function _persistWorkbenchPanelPrefs() {" in state_js
    assert 'id="workbench-attention-strip"' in index_html
    assert "const urgentItems = (groups.attention || []).filter((item) => !item.read).slice(0, 3);" in state_js
    assert 'els.workbenchAttentionStrip?.addEventListener("click", _handleActivityAction);' in state_js
    assert "function _syncActivityToggleButton() {" in state_js
    assert 'toggleWorkbenchPanel("activity");' in state_js
    assert ".workbench-attention-strip {" in style_css
    assert ".workbench-attention-chip {" in style_css


def test_session_history_restores_runtime_feed_from_snapshot():
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")

    assert "export function replaceSessionRuntimeFeed(sessionId, events = []) {" in state_js
    assert 'if (Array.isArray(runtime.recent_events)) replaceSessionRuntimeFeed(sid, runtime.recent_events);' in websocket_js
    assert 'replaceSessionRuntimeFeed(data.session_id, data.runtime.recent_events || []);' in websocket_js
    assert '"runtime": self._session_runtime_snapshot(sid, include_feed=True),' in (ROOT / "hushclaw" / "server_impl.py").read_text(encoding="utf-8")


def test_files_panel_is_integrated_into_workbench_stack():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    files_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-files.css").read_text(encoding="utf-8")
    style_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    responsive_css = (ROOT / "hushclaw" / "web" / "styles" / "responsive.css").read_text(encoding="utf-8")

    assert 'id="files-sidebar" class="workbench-card workbench-section workbench-files hidden"' in index_html
    assert 'title="Open files panel"' in index_html
    assert 'panel?.classList.toggle("hidden", !visible);' in files_js
    assert 'document.body.classList.toggle("files-sidebar-collapsed", _collapsed);' not in files_js
    assert 'localStorage.getItem("hushclaw.ui.files-sidebar-collapsed")' in files_js
    assert 'localStorage.removeItem("hushclaw.ui.files-sidebar-collapsed")' in files_js
    assert "Open files drawer" not in files_js
    assert "#files-sidebar.workbench-files {" in files_css
    assert ".workbench-files {" in style_css
    assert "position: fixed;" not in files_css
    assert "body.files-sidebar-collapsed" not in responsive_css


def test_files_panel_supports_ratings_tags_and_filters():
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    files_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-files.css").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")

    assert 'type: "update_file_metadata"' in files_js
    assert 'id="files-important-filter"' in files_js
    assert 'id="files-tag-filter"' in files_js
    assert 'class="file-rating-star' in files_js
    assert '· Updated ${escHtml(updatedStr)}' not in files_js
    assert ".file-tag-chip" in files_css
    assert ".files-filter-bar" in files_css
    assert 'case "file_metadata_updated":' in websocket_js


def test_files_panel_requests_initial_list_on_connect_and_first_open():
    files_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    websocket_js = (ROOT / "hushclaw" / "web" / "modules" / "websocket.js").read_text(encoding="utf-8")

    assert "let _loadedOnce = false;" in files_js
    assert "let _loadRequested = false;" in files_js
    assert "export function ensureFilesListLoaded({ sync = false } = {}) {" in files_js
    assert 'if (!isWorkbenchPanelPreferredVisible("files") || _loadedOnce || _loadRequested) return;' in files_js
    assert "_sendListFiles();" in files_js
    assert "_loadRequested = true;" in files_js
    assert "_loadedOnce = true;" in files_js
    assert "_loadRequested = false;" in files_js
    assert "ensureFilesListLoaded();" in files_js
    assert "ensureFilesListLoaded({ sync: true });" in websocket_js


def test_workbench_right_rail_uses_compact_density():
    style_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    files_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-files.css").read_text(encoding="utf-8")

    assert "gap: 14px;" in style_css
    assert "padding: 6px 0 4px;" in style_css
    assert "max-height: min(24vh, 220px);" in style_css
    assert "padding: 7px 12px 6px;" in files_css
    assert "padding: 8px 10px 0;" in files_css
    assert "padding: 8px 10px;" in files_css
    assert "min-height: 24px;" in files_css


def test_runtime_bar_hides_when_primary_row_is_empty():
    state_js = (ROOT / "hushclaw" / "web" / "modules" / "state.js").read_text(encoding="utf-8")

    assert 'function _runtimeBarHasContent(label = "", summary = "", badge = "") {' in state_js
    assert 'bar.classList.toggle("hidden", !_runtimeBarHasContent(labelText, summaryText, badgeText));' in state_js
    assert 'bar.classList.add("hidden");' in state_js


def test_collapsed_app_rail_labels_do_not_displace_navigation_icons():
    shell_css = (ROOT / "hushclaw" / "web" / "styles" / "harness-shell.css").read_text(encoding="utf-8")

    selector = "body:not(.app-rail-expanded) .tab > span:not(.nav-update-badge)"
    rule = shell_css.split(selector, 1)[1].split("}", 1)[0]
    assert "position: absolute;" in rule
    assert "width: 1px;" in rule
    assert "clip-path: inset(50%);" in rule
    assert "flex: none;" in rule


def test_harness_shell_has_resizable_conversation_and_details_columns():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    shell_js = (ROOT / "hushclaw" / "web" / "modules" / "shell.js").read_text(encoding="utf-8")
    shell_css = (ROOT / "hushclaw" / "web" / "styles" / "harness-shell.css").read_text(encoding="utf-8")

    assert 'id="sessions-resize-handle"' in index_html
    assert 'id="workbench-resize-handle"' in index_html
    assert 'role="separator"' in index_html
    assert "function wireColumnResize" in shell_js
    assert 'variable: "--threads-drawer-w"' in shell_js
    assert 'variable: "--workbench-w"' in shell_js
    assert 'event.key === "ArrowRight"' in shell_js
    assert ".column-resize-handle" in shell_css
    assert "body.is-resizing-columns" in shell_css


def test_plugin_host_supports_ordered_reversible_ui_slots():
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    host_js = (ROOT / "hushclaw" / "web" / "modules" / "plugin-host.js").read_text(encoding="utf-8")

    for slot in (
        "rail.footer",
        "conversation.header.actions",
        "composer.leading",
        "composer.trailing",
        "workbench.panel",
    ):
        assert f'data-ui-slot="{slot}"' in index_html
    assert "export function registerUiSlot" in host_js
    assert "export function unregisterUiSlot" in host_js
    assert "export function unregisterSidePlugin" in host_js
    assert "return () => unregisterUiSlot(slot, plugin);" in host_js
    assert ".sort((a, b) => (a.order - b.order)" in host_js
    assert "typeof result.cleanup === \"function\"" in host_js

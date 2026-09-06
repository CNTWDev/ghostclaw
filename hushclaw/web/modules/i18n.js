/**
 * i18n.js — Chinese/English language switching.
 * Mirrors the theme.js pattern: localStorage persistence, auto-detect from navigator.language.
 * Default: follows system locale (zh* → Chinese, everything else → English).
 */

export const LOCALES = ["en", "zh"];
export const LOCALE_STORAGE_KEY = "hushclaw.ui.locale";

const LANGS = {
  en: {
    // Navigation tabs
    tab_chat:      "Chat",
    tab_agents:    "Agents",
    tab_skills:    "Skills",
    tab_memories:  "Memories",
    tab_insights:  "Insights",
    tab_tasks:     "Tasks",
    tab_calendar:  "Calendar",
    tab_logs:      "Logs",
    tab_settings:  "Settings",
    // Tab descriptions
    desc_chat:     "Chat with AI agents · manage conversation history",
    desc_agents:   "Create and configure runtime agents",
    desc_skills:   "Install skill packs to extend AI capabilities",
    desc_memories: "Browse and manage the AI's persistent memory",
    desc_insights: "Curated principles and high-signal memory",
    desc_tasks:    "Todos and scheduled recurring tasks",
    desc_calendar: "Calendar — create, view and manage events",
    desc_logs:     "View recent server logs",
    desc_settings: "Configure AI provider, model, and system settings",
    // Sidebar
    sessions:        "Threads",
    files:           "Files",
    search_sessions: "Search sessions…",
    // Memories panel
    mem_kb:          "Knowledge Base",
    mem_profile:     "Profile",
    mem_beliefs:     "Beliefs",
    mem_reflections: "Reflections",
    mem_search:      "Search memories…",
    mem_clean:       "Clean+Compact",
    // Chat input area
    input_placeholder: "Ask Pip…",
    composer_hint: "Use / for skills · @ for agents",
    empty_title: "What are we working on?",
    new_topic: "New Topic",
    export:    "Export",
    // Agents panel
    refresh:       "Refresh",
    new_agent:     "+ New Agent",
    advanced:      "Advanced",
    // Tasks panel
    todos:          "Todos",
    sched_tasks:    "Scheduled Tasks",
    add_todo:       "+ Add",
    add_sched:      "+ Schedule",
    run_once:       "Run once",
    todo_title_ph:  "Todo title…",
    sched_task_ph:  "Task name…",
    sched_prompt_ph:"Prompt / task description…",
    cron_ph:        "cron expr: 0 9 * * *",
    // Calendar toolbar
    new_event_btn: "+ New Event",
    cal_today:     "Today",
    cal_month:     "Month",
    cal_agenda:    "Agenda",
    cal_sync:      "↻ Sync",
    cal_resync:    "⟳ Re-sync",
    cal_prev:      "‹",
    cal_next:      "›",
    cal_weekdays:  ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    // Calendar modal
    new_event:      "New Event",
    edit_event:     "Edit Event",
    all_day:        "All day",
    ev_start:       "Start",
    ev_end:         "End",
    ev_color:       "Color:",
    save:           "Save",
    cancel:         "Cancel",
    delete:         "Delete",
    ev_title_ph:    "Event title *",
    ev_location_ph: "Location",
    ev_desc_ph:     "Description",
    // Settings tabs
    stab_model:        "Model",
    stab_system:       "System",
    stab_memory:       "Memory",
    stab_channels:     "Channels",
    stab_integrations: "Integrations",
    // Header / startup
    logo_tagline:  "Built with Memory, Skills, and Continuous Learning",
    hide:          "Hide",
    done:          "✓ Done",
    handover_msg:  "🔐 Browser opened — complete your action, then click Done",
    session_prefix:"session",
    // WebSocket status
    ws_connecting:    "Connecting to server…",
    ws_retrying:      "Retrying connection…",
    ws_still_waiting: "Still waiting… (attempt {n})",
    ws_starting:      "Server is starting up, this usually takes a few seconds.",
    ws_starting_soon: "Server is starting up — almost there.",
    ws_slow_start:    "Server may be taking longer than usual to start. Keep waiting or check your terminal.",
    ws_reconnecting:  "Connection lost — reconnecting…",
    ws_retry_in:      "retry in {s}s",
    ws_connecting_brief: "connecting…",
    // Settings — Memory tab sections
    smem_workspace_section:   "Workspace & Memory Files",
    smem_context_section:     "Context & Compaction",
    smem_retrieval_section:   "Memory Retrieval",
    smem_decay_section:       "Memory Decay",
    smem_autoextract_section: "Auto-Extraction",
    // Settings — Memory tab fields
    smem_status_label:           "Status:",
    smem_ws_dir_label:           "Workspace Directory",
    smem_optional:               "(optional)",
    smem_history_budget_label:   "History budget (tokens)",
    smem_compact_threshold_label:"Compact threshold",
    smem_keep_turns_label:       "Keep recent turns",
    smem_compact_strategy_label: "Compact strategy",
    smem_min_score_label:        "Min relevance score",
    smem_max_tokens_label:       "Max memory tokens",
    smem_ret_temp_label:         "Retrieval temperature",
    smem_serendipity_label:      "Serendipity budget (fraction)",
    smem_decay_rate_label:       "Decay rate (λ)",
    smem_embed_provider_label:   "Embedding provider",
    smem_embed_model_label:      "Embedding model",
    smem_init_btn:               "🗂 Initialize Workspace (create SOUL.md & USER.md)",
    smem_reseed_btn:             "🔄 Re-seed missing files",
    smem_autoextract_label:      "Enable auto-extraction",
    smem_autoextract_desc:       "Regex-based fact extraction after each turn (zero extra LLM calls)",
    // Settings — Integrations tab
    sint_quickfill:    "Quick-fill provider",
    sint_acct_label:   "Account Label",
    sint_user_email:   "Username / Email",
    sint_app_password: "App Password",
    sint_imap_host:    "IMAP Host",
    sint_port:         "Port",
    sint_smtp_host:    "SMTP Host",
    sint_mailbox:      "Default Mailbox",
    sint_test_conn:    "Test Connection",
    sint_caldav_url:   "CalDAV URL",
    sint_username:     "Username",
    sint_cal_name:     "Calendar Name",
    sint_enabled:      "Enabled",
    sint_optional:     "(optional, e.g. Work)",
    sint_leave_empty:  "(leave empty for all)",
    // Sessions
    no_sessions: "No sessions",
    turns:       "turns",
    // Skills
    skills_refresh: "Refresh",
  },

  zh: {
    tab_chat:      "聊天",
    tab_agents:    "智能体",
    tab_skills:    "技能",
    tab_memories:  "记忆",
    tab_insights:  "洞察",
    tab_tasks:     "任务",
    tab_calendar:  "日历",
    tab_logs:      "日志",
    tab_settings:  "设置",
    desc_chat:     "与 AI 智能体对话 · 管理对话历史",
    desc_agents:   "创建和配置多智能体团队",
    desc_skills:   "安装技能包扩展 AI 能力",
    desc_memories: "浏览和管理 AI 的持久记忆",
    desc_insights: "沉淀高价值观点与方法论",
    desc_tasks:    "待办事项与定时任务",
    desc_calendar: "日历 — 创建、查看和管理事件",
    desc_logs:     "查看最近的服务端日志",
    desc_settings: "配置 AI 提供商、模型和系统设置",
    sessions:        "对话",
    files:           "文件",
    search_sessions: "搜索会话…",
    mem_kb:          "知识库",
    mem_profile:     "用户画像",
    mem_beliefs:     "信念",
    mem_reflections: "复盘",
    mem_search:      "搜索记忆…",
    mem_clean:       "压缩记忆",
    input_placeholder: "和 Pip 说点什么…",
    composer_hint: "输入 / 选择技能 · @ 选择 Agent",
    empty_title: "今天想一起完成什么？",
    new_topic: "新话题",
    export:    "导出",
    refresh:       "刷新",
    new_agent:     "+ 新建智能体",
    advanced:      "高级",
    todos:          "待办",
    sched_tasks:    "定时任务",
    add_todo:       "+ 添加",
    add_sched:      "+ 创建计划",
    run_once:       "执行一次",
    todo_title_ph:  "待办标题…",
    sched_task_ph:  "任务名称…",
    sched_prompt_ph:"提示词 / 任务描述…",
    cron_ph:        "cron 表达式：0 9 * * *",
    new_event_btn: "+ 新建事件",
    cal_today:     "今天",
    cal_month:     "月视图",
    cal_agenda:    "日程",
    cal_sync:      "↻ 同步",
    cal_resync:    "⟳ 重新同步",
    cal_prev:      "‹",
    cal_next:      "›",
    cal_weekdays:  ["日", "一", "二", "三", "四", "五", "六"],
    new_event:      "新建事件",
    edit_event:     "编辑事件",
    all_day:        "全天",
    ev_start:       "开始",
    ev_end:         "结束",
    ev_color:       "颜色：",
    save:           "保存",
    cancel:         "取消",
    delete:         "删除",
    ev_title_ph:    "事件标题 *",
    ev_location_ph: "地点",
    ev_desc_ph:     "描述",
    stab_model:        "模型",
    stab_system:       "系统",
    stab_memory:       "记忆",
    stab_channels:     "频道",
    stab_integrations: "集成",
    // Header / startup
    logo_tagline:  "基于记忆、技能与持续学习构建",
    hide:          "隐藏",
    done:          "✓ 完成",
    handover_msg:  "🔐 浏览器已打开 — 完成操作后点击完成",
    session_prefix:"会话",
    // WebSocket status
    ws_connecting:    "正在连接服务器…",
    ws_retrying:      "正在重试连接…",
    ws_still_waiting: "仍在等待… （第 {n} 次）",
    ws_starting:      "服务器正在启动，通常需要几秒钟。",
    ws_starting_soon: "服务器正在启动 — 即将就绪。",
    ws_slow_start:    "服务器启动时间较长，请继续等待或检查终端。",
    ws_reconnecting:  "连接断开 — 正在重连…",
    ws_retry_in:      "{s}秒后重试",
    ws_connecting_brief: "连接中…",
    // Settings — Memory tab sections
    smem_workspace_section:   "工作区与记忆文件",
    smem_context_section:     "上下文与压缩",
    smem_retrieval_section:   "记忆检索",
    smem_decay_section:       "记忆衰减",
    smem_autoextract_section: "自动提取",
    // Settings — Memory tab fields
    smem_status_label:           "状态：",
    smem_ws_dir_label:           "工作区目录",
    smem_optional:               "（可选）",
    smem_history_budget_label:   "历史预算（词元数）",
    smem_compact_threshold_label:"压缩阈值",
    smem_keep_turns_label:       "保留最近轮次",
    smem_compact_strategy_label: "压缩策略",
    smem_min_score_label:        "最低相关性分数",
    smem_max_tokens_label:       "最大记忆词元数",
    smem_ret_temp_label:         "检索温度",
    smem_serendipity_label:      "随机探索预算（比例）",
    smem_decay_rate_label:       "衰减率（λ）",
    smem_embed_provider_label:   "向量化提供商",
    smem_embed_model_label:      "向量化模型",
    smem_init_btn:               "🗂 初始化工作区（创建 SOUL.md 和 USER.md）",
    smem_reseed_btn:             "🔄 补充缺失文件",
    smem_autoextract_label:      "启用自动提取",
    smem_autoextract_desc:       "每轮对话后正则提取事实（无需额外 LLM 调用）",
    // Settings — Integrations tab
    sint_quickfill:    "快速填充提供商",
    sint_acct_label:   "账户标签",
    sint_user_email:   "用户名 / 邮箱",
    sint_app_password: "应用专用密码",
    sint_imap_host:    "IMAP 服务器",
    sint_port:         "端口",
    sint_smtp_host:    "SMTP 服务器",
    sint_mailbox:      "默认邮箱目录",
    sint_test_conn:    "测试连接",
    sint_caldav_url:   "CalDAV 地址",
    sint_username:     "用户名",
    sint_cal_name:     "日历名称",
    sint_enabled:      "启用",
    sint_optional:     "（可选，如：工作）",
    sint_leave_empty:  "（留空显示全部）",
    // Sessions
    no_sessions: "暂无会话",
    turns:       "轮",
    skills_refresh: "刷新",
  },
};

function _detect() {
  try {
    const saved = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (LOCALES.includes(saved)) return saved;
  } catch { /* ignore */ }
  return (navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en";
}

export let currentLocale = _detect();

/** Translate a key for the current locale, falling back to English then the key itself. */
export function t(key) {
  return (LANGS[currentLocale]?.[key] ?? LANGS.en[key]) ?? key;
}

/** Switch locale, persist to localStorage, apply to DOM, dispatch "locale-changed". */
export function setLocale(lang) {
  if (!LOCALES.includes(lang)) lang = "en";
  currentLocale = lang;
  try { localStorage.setItem(LOCALE_STORAGE_KEY, lang); } catch { /* ignore */ }
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  applyLocale();
  document.dispatchEvent(new CustomEvent("locale-changed", { detail: { locale: lang } }));
}

/** Apply current locale to all annotated DOM elements (idempotent). */
export function applyLocale() {
  // Static text nodes
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const v = t(el.dataset.i18n);
    if (v !== undefined) el.textContent = v;
  });
  // Placeholder attributes
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    const v = t(el.dataset.i18nPh);
    if (v !== undefined) el.placeholder = v;
  });
  // data-desc + title attributes on tab buttons
  document.querySelectorAll("[data-i18n-desc]").forEach(el => {
    const v = t(el.dataset.i18nDesc);
    if (v !== undefined) { el.dataset.desc = v; el.title = v; }
  });
  const messages = document.getElementById("messages");
  if (messages) messages.dataset.emptyTitle = t("empty_title");
  // Calendar weekday row
  const wkdays = document.querySelectorAll(".cal-wkday-label");
  const days = t("cal_weekdays");
  if (Array.isArray(days)) {
    wkdays.forEach((el, i) => { if (days[i] !== undefined) el.textContent = days[i]; });
  }
  // Toggle button label: show the OTHER language (the one you'd switch to)
  const btn = document.getElementById("lang-toggle");
  if (btn) btn.textContent = currentLocale === "zh" ? "EN" : "中";
}

/** Detect locale and apply on page load. */
export function initLocale() {
  document.documentElement.lang = currentLocale === "zh" ? "zh-CN" : "en";
  applyLocale();
}

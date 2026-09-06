/**
 * tasks.js — Tasks panel (todos + scheduled tasks) and its event listeners.
 */

import { state, tasksState, send, escHtml, showToast } from "./state.js";
import { openConfirm } from "./modal.js";
import { applyAiState } from "./ui/ai-primitives.js";

const TODO_PAGE_LIMIT = 30;
const WORK_TASK_STATUS_QUEUED = "queued";
const WORK_TASK_STATUS_RUNNING = "running";
const WORK_TASK_STATUS_BLOCKED = "blocked";
const WORK_TASK_STATUS_STALE = "stale";
const WORK_TASK_RUN_STATUS_RUNNING = "running";
const WORK_TASK_RUNNABLE_STATUSES = [
  WORK_TASK_STATUS_QUEUED,
  WORK_TASK_STATUS_BLOCKED,
  WORK_TASK_STATUS_STALE,
];

// ── Todos ──────────────────────────────────────────────────────────────────

export function refreshTodos(offset = 0) {
  send({ type: "list_todos", offset, limit: tasksState.todoLimit || TODO_PAGE_LIMIT });
}

export function renderTodos(items, hasMore = false, offset = 0) {
  const append = Number(offset || 0) > 0;
  tasksState.todos = append ? [...tasksState.todos, ...items] : items;
  tasksState.todoOffset = Number(offset || 0) + items.length;
  tasksState.todosHasMore = Boolean(hasMore);
  const el = document.getElementById("todos-list");
  if (!el) return;
  el.querySelector(".load-more-row")?.remove();
  if (!tasksState.todos.length) {
    el.innerHTML = '<div class="tasks-empty">No todos yet.</div>';
    return;
  }
  el.innerHTML = "";
  const pending = tasksState.todos.filter(t => t.status !== "done");
  const done    = tasksState.todos.filter(t => t.status === "done");
  [...pending, ...done].forEach(todo => {
    el.appendChild(buildTodoRow(todo));
  });
  if (tasksState.todosHasMore) {
    el.appendChild(buildLoadMoreRow("Load more todos", () => refreshTodos(tasksState.todoOffset)));
  }
}

export function buildTodoRow(todo) {
  const row = document.createElement("div");
  row.className = "todo-row" + (todo.status === "done" ? " done" : "") + (todo.priority ? " high-priority" : "");
  row.dataset.id = todo.todo_id;

  const check = document.createElement("button");
  check.className = "todo-check" + (todo.status === "done" ? " checked" : "");
  check.textContent = todo.status === "done" ? "☑" : "☐";
  check.title = todo.status === "done" ? "Mark as pending" : "Mark as done";
  check.addEventListener("click", () => {
    const newStatus = todo.status === "done" ? "pending" : "done";
    send({ type: "update_todo", todo_id: todo.todo_id, status: newStatus });
  });

  const title = document.createElement("span");
  title.className = "todo-title";
  title.textContent = todo.title;

  const meta = document.createElement("span");
  meta.className = "todo-meta";
  if (todo.priority) {
    const badge = document.createElement("span");
    badge.className = "priority-badge";
    badge.textContent = "!";
    meta.appendChild(badge);
  }
  if (todo.due_at) {
    const due = document.createElement("span");
    due.className = "todo-due";
    const d = new Date(todo.due_at * 1000);
    due.textContent = d.toLocaleDateString();
    meta.appendChild(due);
  }

  const del = document.createElement("button");
  del.className = "todo-del icon-btn secondary";
  del.textContent = "✕";
  del.title = "Delete todo";
  del.addEventListener("click", async () => {
    const confirmed = await openConfirm({
      title: "Delete todo",
      message: `Delete "${todo.title.slice(0, 80)}${todo.title.length > 80 ? "…" : ""}"?`,
      confirmText: "Delete",
      cancelText: "Cancel",
      dangerConfirm: true,
    });
    if (!confirmed) return;
    send({ type: "delete_todo", todo_id: todo.todo_id });
    row.remove();
  });

  row.appendChild(check);
  row.appendChild(title);
  row.appendChild(meta);
  row.appendChild(del);
  return row;
}

export function onTodoCreated(item) {
  if (!item) return;
  refreshTodos(0);
}

export function onTodoUpdated(item) {
  if (!item) return;
  refreshTodos(0);
}

export function onTodoDeleted(todo_id, ok) {
  if (ok) {
    tasksState.todos = tasksState.todos.filter(t => t.todo_id !== todo_id);
    renderTodos(tasksState.todos, tasksState.todosHasMore, 0);
  }
}

function buildLoadMoreRow(label, onClick) {
  const wrap = document.createElement("div");
  wrap.className = "load-more-row tasks-load-more";
  const btn = document.createElement("button");
  btn.className = "secondary load-more-btn";
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  wrap.appendChild(btn);
  return wrap;
}

// ── Work tasks ─────────────────────────────────────────────────────────────

export function renderWorkTasks(items, reliability = null) {
  tasksState.work = items;
  if (reliability) tasksState.reliability = reliability;
  renderWorkTaskHealth(tasksState.reliability);
  const filter = document.getElementById("work-task-status-filter");
  if (filter && filter.value !== tasksState.workStatus) filter.value = tasksState.workStatus;
  const el = document.getElementById("work-tasks-list");
  if (!el) return;
  if (!items.length) {
    el.innerHTML = tasksState.workStatus
      ? `<div class="tasks-empty"><strong>No ${escHtml(tasksState.workStatus)} work tasks</strong><span>Change the filter or create a new task.</span></div>`
      : '<div class="tasks-empty"><strong>No work tasks yet</strong><span>Create one when you want HushClaw to track a background run.</span></div>';
    return;
  }
  el.innerHTML = "";
  items.forEach(task => el.appendChild(buildWorkTaskRow(task)));
}

function renderWorkTaskHealth(reliability) {
  const el = document.getElementById("work-task-health");
  if (!el) return;
  const tasks = reliability?.tasks || {};
  const delivery = reliability?.delivery || {};
  const attention = Number(tasks.blocked || 0) + Number(tasks.stale || 0) + Number(tasks.lease_at_risk || 0);
  const deliveryAttention = Number(delivery.retry || 0) + Number(delivery.dead_letter || 0);
  const stateClass = attention + deliveryAttention > 0 ? "needs-attention" : "healthy";
  el.className = `work-task-health ${stateClass}`;
  el.innerHTML = `
    <span class="work-task-health-label">${attention + deliveryAttention > 0 ? "Needs attention" : "Reliable"}</span>
    <span><strong>${Number(tasks.running || 0)}</strong> running</span>
    <span><strong>${attention}</strong> blocked / stale</span>
    <span><strong>${Number(tasks.done || 0)}</strong> verified</span>
    <span><strong>${deliveryAttention}</strong> delivery retry / dead-letter</span>`;
}

export function refreshWorkTasks() {
  send({ type: "list_work_tasks", status: tasksState.workStatus || "" });
}

export function buildWorkTaskRow(task) {
  const row = document.createElement("div");
  row.className = "todo-row work-task-row";
  row.dataset.id = task.task_id;
  const taskStatus = task.status || WORK_TASK_STATUS_QUEUED;
  applyAiState(row, taskStatus, { label: `${task.title || task.task_id}: ${taskStatus}` });

  const status = document.createElement("span");
  status.className = `work-task-status work-task-status--${taskStatus}`;
  status.textContent = taskStatus;

  const body = document.createElement("div");
  body.className = "todo-body";
  const title = document.createElement("div");
  title.className = "todo-title";
  title.textContent = task.title || task.task_id;
  const meta = document.createElement("div");
  meta.className = "todo-meta";
  const run = (task.runs || [])[0] || null;
  const evidence = Array.isArray(run?.evidence) ? run.evidence : [];
  const contract = task.metadata?.completion_contract || {};
  const blockedBy = Array.isArray(task.blocked_by) ? task.blocked_by : [];
  meta.textContent = [
    task.workspace ? `workspace ${task.workspace}` : "",
    task.model_override ? `model ${task.model_override}` : "",
    run ? `attempt ${run.attempt || 1} · ${run.status}` : "",
    run?.completion_state ? `proof ${run.completion_state}` : `proof ${contract.proof_required || "response"}`,
    evidence.length ? `${evidence.length} evidence` : "",
    blockedBy.length ? `waiting for ${blockedBy.length} task${blockedBy.length === 1 ? "" : "s"}` : "",
    run?.session_id ? `session ${run.session_id}` : "",
    run?.updated ? `updated ${formatTaskTime(run.updated)}` : "",
    run?.error_fingerprint ? `error ${run.error_fingerprint}` : "",
  ].filter(Boolean).join(" · ") || task.task_id;
  body.appendChild(title);
  body.appendChild(meta);

  title.style.cursor = "pointer";
  title.addEventListener("click", () => {
    const existing = row.querySelector(".sched-prompt-preview");
    if (existing) { existing.remove(); return; }
    const pre = document.createElement("div");
    pre.className = "sched-prompt-preview";
    const parts = [];
    if (task.spec) parts.push(`Spec:\n${task.spec}`);
    const criteria = Array.isArray(contract.criteria) ? contract.criteria : [];
    if (criteria.length) parts.push(`Done when:\n${criteria.map(item => `- ${item}`).join("\n")}`);
    parts.push(`Required proof: ${contract.proof_required || "response"}`);
    if (run?.result) parts.push(`Result:\n${run.result}`);
    if (run?.error) parts.push(`Error:\n${run.error}`);
    if (run?.error_fingerprint) parts.push(`Error fingerprint: ${run.error_fingerprint}`);
    if (run?.session_id) parts.push(`Session: ${run.session_id}`);
    if (evidence.length) {
      parts.push(`Evidence:\n${evidence.map(item => `- ${item.kind || "evidence"}${item.tool ? ` · ${item.tool}` : ""}${item.title ? ` · ${item.title}` : ""}`).join("\n")}`);
    }
    pre.textContent = parts.join("\n\n") || "No task details yet.";
    body.appendChild(pre);
  });

  const actions = document.createElement("div");
  actions.className = "sched-actions";
  const openSessionBtn = document.createElement("button");
  openSessionBtn.className = "secondary small work-task-action";
  openSessionBtn.textContent = "Session";
  openSessionBtn.disabled = !run?.session_id;
  openSessionBtn.title = run?.session_id ? "Open linked run session" : "No linked session yet";
  openSessionBtn.addEventListener("click", () => {
    const sessionId = run?.session_id || "";
    if (!sessionId) return;
    import("./panels.js").then(({ switchTab, loadSession }) => {
      switchTab("chat");
      loadSession(sessionId);
    }).catch(() => {
      showToast("Unable to open linked session.", "error");
    });
  });
  const runBtn = document.createElement("button");
  runBtn.className = "secondary small work-task-action work-task-action-primary";
  runBtn.textContent = "Run";
  runBtn.disabled = !WORK_TASK_RUNNABLE_STATUSES.includes(taskStatus) || blockedBy.length > 0;
  runBtn.title = blockedBy.length
    ? "Dependencies must finish first"
    : (runBtn.disabled ? `Cannot run while task is ${taskStatus}` : "Run this task now");
  runBtn.addEventListener("click", () => {
    send({ type: "run_work_task_now", task_id: task.task_id, agent: "default" });
    runBtn.textContent = "Running";
    setTimeout(() => { runBtn.textContent = "Run"; }, 2000);
  });
  const claimBtn = document.createElement("button");
  claimBtn.className = "secondary small work-task-action";
  claimBtn.textContent = "Claim";
  claimBtn.disabled = !WORK_TASK_RUNNABLE_STATUSES.includes(taskStatus) || blockedBy.length > 0;
  claimBtn.title = claimBtn.disabled ? `Cannot claim while task is ${taskStatus}` : "Claim without running it automatically";
  claimBtn.addEventListener("click", () => {
    send({ type: "claim_work_task", task_id: task.task_id, worker_id: "webui" });
  });
  const doneBtn = document.createElement("button");
  doneBtn.className = "secondary small work-task-action";
  doneBtn.textContent = "Done";
  doneBtn.disabled = !run || run.status !== WORK_TASK_RUN_STATUS_RUNNING;
  doneBtn.title = doneBtn.disabled ? "Only a running task run can be completed" : "Mark the current run as completed";
  doneBtn.addEventListener("click", () => {
    if (!run) return;
    send({
      type: "complete_work_task",
      run_id: run.run_id,
      lease_token: run.lease_token || "",
      result: "Completed from WebUI",
    });
  });
  const retryBtn = document.createElement("button");
  retryBtn.className = "secondary small work-task-action";
  retryBtn.textContent = "Retry";
  retryBtn.disabled = taskStatus === WORK_TASK_STATUS_RUNNING || taskStatus === WORK_TASK_STATUS_QUEUED;
  retryBtn.title = retryBtn.disabled ? `Retry is available after a task is blocked, stale, or done` : "Move this task back to queued";
  retryBtn.addEventListener("click", () => {
    send({ type: "retry_work_task", task_id: task.task_id });
  });
  actions.appendChild(openSessionBtn);
  actions.appendChild(runBtn);
  actions.appendChild(claimBtn);
  actions.appendChild(doneBtn);
  actions.appendChild(retryBtn);

  row.appendChild(status);
  row.appendChild(body);
  row.appendChild(actions);
  return row;
}

function formatTaskTime(ts) {
  const n = Number(ts || 0);
  if (!n) return "";
  const d = new Date(n * 1000);
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function onWorkTaskCreated(task) {
  if (!task) return;
  if (tasksState.workStatus && task.status !== tasksState.workStatus) {
    refreshWorkTasks();
    return;
  }
  tasksState.work.unshift(task);
  renderWorkTasks(tasksState.work);
}

function submitWorkTask() {
  const titleEl = document.getElementById("work-task-title-input");
  const specEl = document.getElementById("work-task-spec-input");
  const modelEl = document.getElementById("work-task-model-input");
  const contractEl = document.getElementById("work-task-contract-input");
  const proofEl = document.getElementById("work-task-proof-input");
  const title = titleEl?.value.trim() || "";
  const spec = specEl?.value.trim() || "";
  const model = modelEl?.value.trim() || "";
  const criterion = contractEl?.value.trim() || "";
  const proofRequired = proofEl?.value || "response";
  if (!title) return;
  send({
    type: "create_work_task",
    title,
    spec,
    model_override: model,
    acceptance_criteria: criterion ? [criterion] : [],
    proof_required: proofRequired,
  });
  if (titleEl) titleEl.value = "";
  if (specEl) specEl.value = "";
  if (modelEl) modelEl.value = "";
  if (contractEl) contractEl.value = "";
  if (proofEl) proofEl.value = "response";
  document.getElementById("work-task-add-row")?.classList.add("hidden");
  tasksState.addingWork = false;
}

function submitTodo() {
  const titleEl = document.getElementById("todo-title-input");
  const dueEl   = document.getElementById("todo-due-input");
  const title   = titleEl?.value.trim();
  if (!title) return;
  let due_at = null;
  if (dueEl?.value) {
    due_at = Math.floor(new Date(dueEl.value + "T00:00:00").getTime() / 1000);
  }
  send({
    type: "create_todo",
    title,
    priority: tasksState.todoPriority ? 1 : 0,
    due_at,
    tags: [],
  });
  if (titleEl) titleEl.value = "";
  if (dueEl)   dueEl.value = "";
  tasksState.todoPriority = false;
  document.getElementById("todo-priority-btn")?.classList.remove("active");
  document.getElementById("todo-add-row")?.classList.add("hidden");
}

// ── Scheduled tasks ────────────────────────────────────────────────────────

export function renderScheduledTasks(tasks) {
  tasksState.scheduled = tasks;
  const el = document.getElementById("scheduled-list");
  if (!el) return;
  if (!tasks.length) {
    el.innerHTML = '<div class="tasks-empty">No scheduled tasks yet.</div>';
    return;
  }
  el.innerHTML = "";
  tasks.forEach(task => el.appendChild(buildSchedRow(task)));
}

export function buildSchedRow(task) {
  const row = document.createElement("div");
  row.className = "sched-row" + (task.enabled ? "" : " disabled");
  row.dataset.id = task.id;

  const icon = document.createElement("span");
  icon.className = "sched-icon";
  icon.textContent = task.run_once ? "⚡" : "⟳";
  icon.title = task.run_once ? "One-shot task" : "Recurring task";

  const info = document.createElement("div");
  info.className = "sched-info";
  const name = document.createElement("span");
  name.className = "sched-name";
  name.textContent = task.title || task.prompt.slice(0, 50);
  const cronSpan = document.createElement("span");
  cronSpan.className = "sched-cron";
  cronSpan.textContent = task.cron;
  info.appendChild(name);
  info.appendChild(cronSpan);

  name.style.cursor = "pointer";
  name.addEventListener("click", () => {
    const existing = row.querySelector(".sched-prompt-preview");
    if (existing) { existing.remove(); return; }
    const pre = document.createElement("div");
    pre.className = "sched-prompt-preview";
    pre.textContent = task.prompt;
    info.appendChild(pre);
  });

  const actions = document.createElement("div");
  actions.className = "sched-actions";

  const toggleBtn = document.createElement("button");
  toggleBtn.className = "secondary small";
  toggleBtn.textContent = task.enabled ? "⏸" : "▶";
  toggleBtn.title = task.enabled ? "Pause" : "Resume";
  toggleBtn.addEventListener("click", () => {
    send({ type: "toggle_scheduled_task", task_id: task.id, enabled: !task.enabled });
  });

  const runBtn = document.createElement("button");
  runBtn.className = "secondary small";
  runBtn.textContent = "▷";
  runBtn.title = "Run now";
  runBtn.addEventListener("click", () => {
    send({ type: "run_scheduled_task_now", task_id: task.id });
    runBtn.textContent = "…";
    setTimeout(() => { runBtn.textContent = "▷"; }, 2000);
  });

  const delBtn = document.createElement("button");
  delBtn.className = "danger small";
  delBtn.textContent = "✕";
  delBtn.title = "Delete";
  delBtn.addEventListener("click", async () => {
    const full = (task.title || task.prompt || "").trim();
    const label = full.slice(0, 80);
    const confirmed = await openConfirm({
      title: "Delete scheduled task",
      message: full
        ? `Delete scheduled task "${label}${full.length > 80 ? "…" : ""}"?`
        : "Delete this scheduled task?",
      confirmText: "Delete",
      cancelText: "Cancel",
      dangerConfirm: true,
    });
    if (!confirmed) return;
    send({ type: "delete_scheduled_task", task_id: task.id });
    row.remove();
  });

  actions.appendChild(toggleBtn);
  actions.appendChild(runBtn);
  actions.appendChild(delBtn);

  row.appendChild(icon);
  row.appendChild(info);
  row.appendChild(actions);
  return row;
}

export function onTaskCreated(task) {
  if (!task) return;
  tasksState.scheduled.push(task);
  renderScheduledTasks(tasksState.scheduled);
}

export function onTaskToggled(task_id, enabled, ok) {
  if (!ok) return;
  const t = tasksState.scheduled.find(t => t.id === task_id);
  if (t) {
    t.enabled = enabled ? 1 : 0;
    renderScheduledTasks(tasksState.scheduled);
  }
}

export function populateSchedAgentSelect() {
  const sel = document.getElementById("sched-agent-select");
  if (!sel) return;
  sel.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = "default";
  sel.appendChild(defaultOpt);
  state.agents.forEach(a => {
    if (a.name === "default") return;
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name;
    sel.appendChild(opt);
  });
}

export function buildCronFromSimple() {
  const freq = document.getElementById("sched-freq")?.value || "daily";
  const time = document.getElementById("sched-time")?.value || "09:00";
  const [h, m] = time.split(":").map(Number);
  if (freq === "hourly") return `${m} * * * *`;
  if (freq === "weekly") return `${m} ${h} * * 1`;
  return `${m} ${h} * * *`;
}

function submitSchedTask() {
  const title  = document.getElementById("sched-title-input")?.value.trim() || "";
  const prompt = document.getElementById("sched-prompt-input")?.value.trim() || "";
  if (!prompt) return;
  const modeEl = document.querySelector("input[name='sched-mode']:checked");
  const mode   = modeEl?.value || "simple";
  let cron;
  if (mode === "cron") {
    cron = document.getElementById("sched-cron-expr")?.value.trim() || "0 9 * * *";
  } else {
    cron = buildCronFromSimple();
  }
  const agent   = document.getElementById("sched-agent-select")?.value || "";
  const runOnce = document.getElementById("sched-run-once")?.checked || false;
  send({ type: "create_scheduled_task", title, cron, prompt, agent, run_once: runOnce });
  const titleEl  = document.getElementById("sched-title-input");
  const promptEl = document.getElementById("sched-prompt-input");
  if (titleEl)  titleEl.value = "";
  if (promptEl) promptEl.value = "";
  document.getElementById("sched-add-row")?.classList.add("hidden");
}

// ── Tasks event listeners ──────────────────────────────────────────────────

document.getElementById("btn-add-todo")?.addEventListener("click", () => {
  const row = document.getElementById("todo-add-row");
  if (!row) return;
  tasksState.addingTodo = true;
  tasksState.todoPriority = false;
  row.classList.remove("hidden");
  document.getElementById("todo-priority-btn")?.classList.remove("active");
  document.getElementById("todo-title-input")?.focus();
});

document.getElementById("btn-todo-cancel")?.addEventListener("click", () => {
  document.getElementById("todo-add-row")?.classList.add("hidden");
  tasksState.addingTodo = false;
});

document.getElementById("todo-priority-btn")?.addEventListener("click", (e) => {
  tasksState.todoPriority = !tasksState.todoPriority;
  e.target.classList.toggle("active", tasksState.todoPriority);
});

document.getElementById("btn-todo-submit")?.addEventListener("click", submitTodo);

document.getElementById("todo-title-input")?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.isComposing) submitTodo();
  if (ev.key === "Escape") document.getElementById("btn-todo-cancel")?.click();
});

document.getElementById("btn-add-work-task")?.addEventListener("click", () => {
  const row = document.getElementById("work-task-add-row");
  if (!row) return;
  tasksState.addingWork = true;
  row.classList.remove("hidden");
  document.getElementById("work-task-title-input")?.focus();
});

document.getElementById("btn-work-task-cancel")?.addEventListener("click", () => {
  document.getElementById("work-task-add-row")?.classList.add("hidden");
  tasksState.addingWork = false;
});

document.getElementById("btn-work-task-submit")?.addEventListener("click", submitWorkTask);

document.getElementById("work-task-status-filter")?.addEventListener("change", (ev) => {
  tasksState.workStatus = ev.target?.value || "";
  refreshWorkTasks();
});

document.getElementById("work-task-title-input")?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.isComposing) submitWorkTask();
  if (ev.key === "Escape") document.getElementById("btn-work-task-cancel")?.click();
});

document.getElementById("btn-add-scheduled")?.addEventListener("click", () => {
  const row = document.getElementById("sched-add-row");
  if (!row) return;
  row.classList.remove("hidden");
  populateSchedAgentSelect();
  document.getElementById("sched-title-input")?.focus();
});

document.getElementById("btn-sched-cancel")?.addEventListener("click", () => {
  document.getElementById("sched-add-row")?.classList.add("hidden");
});

document.querySelectorAll("input[name='sched-mode']").forEach(radio => {
  radio.addEventListener("change", () => {
    const isCron = radio.value === "cron";
    document.getElementById("sched-simple-inputs")?.classList.toggle("hidden", isCron);
    document.getElementById("sched-cron-input")?.classList.toggle("hidden", !isCron);
  });
});

document.getElementById("btn-sched-submit")?.addEventListener("click", submitSchedTask);

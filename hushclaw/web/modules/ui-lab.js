import {
  AI_STATES,
  applyAiState,
  createAgentActivity,
  createContextCard,
  createProcessDisclosure,
} from "./ui/ai-primitives.js";
import { initComposerMenu } from "./ui/composer-menu.js";
import { initSelectionActions } from "./ui/selection-actions.js";
import { openConfirm } from "./modal.js";

const activity = createAgentActivity({ label: "Searching memory…", detail: "12 notes", startedAt: Date.now() });
document.getElementById("lab-activity").appendChild(activity);

const stateLabels = {
  queued: "Queued",
  running: "Searching memory…",
  waiting_user: "Waiting for your confirmation",
  completed: "Memory retrieved",
  failed: "Memory search failed",
};
const buttonHost = document.querySelector('[data-target="activity"]');
for (const state of [AI_STATES.QUEUED, AI_STATES.RUNNING, AI_STATES.WAITING_USER, AI_STATES.COMPLETED, AI_STATES.FAILED]) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary small";
  button.textContent = state;
  button.addEventListener("click", () => activity.updateActivity({ state, label: stateLabels[state], startedAt: Date.now() }));
  buttonHost.appendChild(button);
}
setInterval(() => activity.updateActivity({}), 1000);

const process = createProcessDisclosure({ index: "R1/3", label: "3 actions · complete", state: AI_STATES.COMPLETED });
process.body.innerHTML = `
  <div class="ui-lab-tool"><span>Search</span><strong>Found 8 relevant sources</strong></div>
  <div class="ui-lab-tool"><span>Read</span><strong>Compared current implementation</strong></div>
  <div class="ui-lab-tool"><span>Verify</span><strong>All checks passed</strong></div>`;
document.getElementById("lab-process").appendChild(process.root);

const taskHost = document.getElementById("lab-tasks");
for (const [state, title, detail] of [
  [AI_STATES.RUNNING, "Research interaction patterns", "2 of 4 sources reviewed"],
  [AI_STATES.WAITING_USER, "Publish proposed changes", "Waiting for approval"],
  [AI_STATES.COMPLETED, "Verify migration", "12 checks passed"],
]) {
  const row = document.createElement("div");
  row.className = "work-task-row ui-lab-task";
  row.innerHTML = `<span class="work-task-status">${state.replace("_", " ")}</span><span><strong>${title}</strong><small>${detail}</small></span>`;
  applyAiState(row, state, { label: `${title}: ${state}` });
  taskHost.appendChild(row);
}

const contextHost = document.getElementById("lab-context");
contextHost.append(
  createContextCard({ label: "File", title: "response-policy.md", detail: "Concise answer structure", meta: "1.8k chars" }),
  createContextCard({ label: "Web", title: "AI interaction primitives", detail: "Progressive disclosure and approvals", meta: "Source 2" }),
);

document.getElementById("lab-approval").addEventListener("click", () => openConfirm({
  title: "Apply three file changes?",
  message: "The changes are ready and verified. You can review the details before applying them.",
  confirmText: "Apply changes",
  cancelText: "Not now",
}));

const input = document.getElementById("lab-input");
initComposerMenu({
  button: document.getElementById("lab-add"),
  input,
  onUpload: () => { input.placeholder = "Upload would open here"; },
  onBrowseFiles: () => { input.placeholder = "Workspace files would open here"; },
});
initSelectionActions({ messages: document.querySelector(".ui-lab-composer-demo"), input });

document.getElementById("lab-theme").addEventListener("click", (event) => {
  const root = document.documentElement;
  const dark = root.dataset.mode !== "dark";
  root.dataset.mode = dark ? "dark" : "light";
  event.currentTarget.textContent = dark ? "Light mode" : "Dark mode";
});

/**
 * settings/tab-system.js — System tab renderer (Generation, Appearance,
 * Pricing, Developer Mode, Updates, Browser, Skills Dirs, Workspaces, Tools).
 */

import { wizard, browser, calendarCfg, els, escHtml, showToast } from "../state.js";
import {
  bindThemeControls, getThemeMode,
} from "../theme.js";
import {
  confirmAndForceUpgrade, confirmAndRunUpdate, refreshUpdateUi, requestCheckUpdate,
} from "../updates.js";
import { refreshNavUpdateAction } from "../nav_update.js";
import { syncFormToState } from "./save.js";

export function renderSystemTab() {
  const themeMode  = wizard.themeMode || getThemeMode();
  const customPrompt = !wizard.systemPromptDefault;
  const apiKeyRegistry = Array.isArray(wizard.apiKeyRegistry) ? wizard.apiKeyRegistry : [];
  const apiKeyCards = apiKeyRegistry.map((item) => {
    const key = String(item.key || "");
    const configured = Boolean(item.configured);
    const source = String(item.source || "unset");
    const canClear = Boolean(item.can_clear_saved);
    const sourceLabel = source === "env"
      ? "environment variable"
      : source === "secret_store"
        ? "saved securely"
        : source === "config"
          ? "saved in config"
          : "not configured";
    const links = [];
    if (item.manage_url) {
      links.push(`
        <a href="${escHtml(item.manage_url)}" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent)">
          Get Key ↗
        </a>
      `);
    }
    if (item.docs_url && item.docs_url !== item.manage_url) {
      links.push(`
        <a href="${escHtml(item.docs_url)}" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent)">
          Docs ↗
        </a>
      `);
    }
    return `
      <div class="wfield" data-api-key-row="${escHtml(key)}">
        <label>${escHtml(item.label || key)}</label>
        <input type="password"
               class="sys-api-key-input"
               data-api-key="${escHtml(key)}"
               placeholder="${configured ? "Leave blank to keep the current value" : "Paste the credential value"}"
               value="">
        <div class="wfield-hint">
          Status: <strong>${configured ? "configured" : "not configured"}</strong> via ${escHtml(sourceLabel)}.
          ${item.description ? escHtml(item.description) : "Saved values are never sent back to the browser in plaintext."}
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center">
          ${links.join("")}
          ${canClear ? `
            <label class="connector-inline" style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)">
              <input type="checkbox" class="sys-api-key-clear" data-api-key="${escHtml(key)}">
              <span>Clear saved value on next save</span>
            </label>
          ` : ""}
        </div>
      </div>
    `;
  }).join("");
  els.wizardBody.innerHTML = `
    <div class="settings-section">
      <h3 class="settings-section-h">Language &amp; Region</h3>
      <div class="wfield">
        <label>Timezone</label>
        <input id="sys-timezone" type="text" list="sys-tz-list"
               value="${escHtml(calendarCfg.timezone)}"
               placeholder="Auto-detected from browser">
        <datalist id="sys-tz-list">
          ${(Intl.supportedValuesOf?.("timeZone") ?? []).map(tz => `<option value="${tz}"></option>`).join("")}
        </datalist>
        <div class="wfield-hint">
          Applied system-wide: AI interprets relative times ("3pm tomorrow") in this timezone,
          and all calendar dates are displayed in it. Auto-detected from your browser on first use.
        </div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Generation</h3>
      <div class="wfield">
        <label>Max output tokens</label>
        <input type="number" id="sys-max-tokens" min="0" max="32768" step="256"
               value="${escHtml(String(wizard.maxTokens))}">
        <div class="wfield-hint">Maximum tokens the model generates per response. Set 0 to remove app-side cap (provider default still applies).</div>
      </div>
      <div class="wfield">
        <label>Max tool rounds</label>
        <input type="number" id="sys-max-tool-rounds" min="1" max="20" step="1"
               value="${escHtml(String(wizard.maxToolRounds))}">
        <div class="wfield-hint">Maximum tool rounds before one final no-tool summary call. HushClaw caps this safety limit at 20.</div>
      </div>
      <div class="wfield">
        <label>System prompt</label>
        <div class="connector-row">
          <div class="connector-meta">
            <span class="connector-name">${customPrompt ? "Custom prompt" : "Built-in default"}</span>
            <span class="connector-desc">${customPrompt ? "This installation overrides the built-in agent prompt." : "HushClaw keeps the default prompt in code and does not write it to your config file."}</span>
          </div>
          <button type="button" id="sys-system-prompt-customize" class="secondary small" style="${customPrompt ? "display:none" : ""}">Customize</button>
          <button type="button" id="sys-system-prompt-reset" class="secondary small" style="${customPrompt ? "" : "display:none"}">Reset to default</button>
        </div>
        <textarea id="sys-system-prompt" rows="8"
                  style="width:100%;box-sizing:border-box;resize:vertical;margin-top:8px;${customPrompt ? "" : "display:none"}"
                  placeholder="Custom system prompt">${escHtml(customPrompt ? wizard.systemPrompt : "")}</textarea>
        <div class="wfield-hint">${customPrompt ? "Save an empty prompt or reset to return to the built-in default." : "Most users should keep this default. Customize only when this installation needs a different base persona."}</div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Appearance</h3>
      <div class="wfield">
        <label>Brightness</label>
        <p class="wdesc" style="margin:0 0 6px">The HushClaw design system is fixed. Auto follows your OS appearance setting.</p>
        <div class="theme-mode-group" role="radiogroup" aria-label="Theme mode">
          <label class="theme-mode-option">
            <input type="radio" name="ui-theme-mode" value="auto" ${themeMode === "auto" ? "checked" : ""}>
            <span>Auto (System)</span>
          </label>
          <label class="theme-mode-option">
            <input type="radio" name="ui-theme-mode" value="light" ${themeMode === "light" ? "checked" : ""}>
            <span>Light</span>
          </label>
          <label class="theme-mode-option">
            <input type="radio" name="ui-theme-mode" value="dark" ${themeMode === "dark" ? "checked" : ""}>
            <span>Dark</span>
          </label>
        </div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Pricing <span class="wfield-optional">(optional)</span></h3>
      <p class="wdesc">Used for cost estimation in the chat UI. Set to 0.0 to disable.</p>
      <div class="wfield">
        <label>Input cost (USD / 1k tokens)</label>
        <input type="number" id="sys-cost-in" min="0" step="0.0001"
               value="${escHtml(String(wizard.costIn))}">
      </div>
      <div class="wfield">
        <label>Output cost (USD / 1k tokens)</label>
        <input type="number" id="sys-cost-out" min="0" step="0.0001"
               value="${escHtml(String(wizard.costOut))}">
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Developer Mode</h3>
      <div class="connector-row">
        <div class="connector-meta">
          <span class="connector-name">Extended runtime diagnostics</span>
          <span class="connector-desc">Keep the chat layout unchanged; add tool input/result previews to the Runtime monitor and enable advanced update controls</span>
        </div>
        <label class="toggle">
          <input type="checkbox" id="sys-dev-mode" ${(() => { try { return localStorage.getItem("hushclaw.dev.mode") === "1"; } catch { return false; } })() ? "checked" : ""}>
          <span class="slider"></span>
        </label>
      </div>
      <div style="margin-top:10px">
        <a href="/ui-lab.html" target="_blank" rel="noopener" class="settings-inline-link">Open AI interaction UI Lab ↗</a>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Updates</h3>
      <p class="wdesc">Check GitHub releases and upgrade after your confirmation.</p>
      <div class="connector-row">
        <div class="connector-meta">
          <span class="connector-name">Auto-check for updates</span>
          <span class="connector-desc">Background check based on interval</span>
        </div>
        <label class="toggle">
          <input type="checkbox" id="upd-auto-check" ${wizard.updateAutoCheckEnabled ? "checked" : ""}>
          <span class="slider"></span>
        </label>
      </div>
      <div class="wfield" style="margin-top:8px">
        <label>Check interval (hours)</label>
        <input type="number" id="upd-interval-hours" min="1" max="168" step="1"
               value="${escHtml(String(wizard.updateCheckIntervalHours || 24))}">
      </div>
      <div class="wfield">
        <label>Channel</label>
        <select id="upd-channel">
          <option value="stable" ${wizard.updateChannel === "stable" ? "selected" : ""}>stable</option>
          <option value="prerelease" ${wizard.updateChannel === "prerelease" ? "selected" : ""}>prerelease</option>
        </select>
      </div>
      <div id="upd-status" class="wfield-hint" style="margin-top:6px"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
        <button type="button" id="upd-check-btn" class="secondary">Check now</button>
        <button type="button" id="upd-upgrade-btn" class="secondary">Upgrade now</button>
        <button type="button" id="upd-force-btn" class="secondary" style="display:none;opacity:.7;font-size:11px" title="Dev mode: skip version check and trigger upgrade immediately">Force Upgrade ⚡</button>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">API Rate Limits</h3>
      <p class="wdesc">
        HushClaw does not control provider-side rate limits or credit quotas.
        If you see errors like "Key limit exceeded" (e.g., on OpenRouter), manage your
        limits directly on your provider's dashboard.
      </p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
        <a href="https://openrouter.ai/settings/keys" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent)">
          OpenRouter Key Settings ↗
        </a>
        <a href="https://platform.openai.com/usage" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent)">
          OpenAI Usage ↗
        </a>
        <a href="https://console.anthropic.com" target="_blank" rel="noopener"
           style="padding:5px 12px;border-radius:var(--radius);border:1px solid var(--border);
                  text-decoration:none;font-size:12px;color:var(--accent)">
          Anthropic Console ↗
        </a>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Tool API Keys</h3>
      <p class="wdesc">
        Configure credentials for built-in tools and future skills. HushClaw stores saved values in its local secret store and keeps only stable references in
        <code>[api_keys]</code>. The browser only sees configured/unconfigured status and metadata.
      </p>
      ${apiKeyCards || `<div class="wfield-hint">No tool API keys are registered yet.</div>`}
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Browser</h3>
      <p class="wdesc">
        Enables JS-rendered page fetching, clicking, form filling, and screenshots.
        Playwright (Chromium) is installed automatically on first use.
      </p>
      <div class="connector-row">
        <div class="connector-meta">
          <span class="connector-name">Enable browser tools</span>
          <span class="connector-badge ${browser.playwright_installed ? 'badge-set' : ''}">
            ${browser.playwright_installed ? 'playwright installed' : 'auto-install on first use'}
          </span>
        </div>
        <label class="toggle">
          <input type="checkbox" id="br-enabled" ${browser.enabled ? 'checked' : ''}
                 onchange="document.getElementById('br-fields').style.display=this.checked?'':'none'">
          <span class="slider"></span>
        </label>
      </div>
      <div id="br-fields" style="${browser.enabled ? '' : 'display:none'}">
        <div class="connector-row">
          <div class="connector-meta">
            <span class="connector-name">Headless mode</span>
            <span class="connector-desc">Hide browser window (disable for debugging)</span>
          </div>
          <label class="toggle">
            <input type="checkbox" id="br-headless" ${browser.headless ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
        </div>
        <div class="wfield" style="margin-top:8px">
          <label>Operation timeout (seconds)</label>
          <input type="number" id="br-timeout" min="5" max="120" step="5"
                 value="${browser.timeout}">
        </div>
        <div class="connector-row" style="margin-top:10px">
          <div class="connector-meta">
            <span class="connector-name">Use My Chrome</span>
            <span class="connector-desc">
              Connect HushClaw to your real Google Chrome over the Chrome DevTools Protocol (CDP).
              Uses your normal Chrome profile (cookies and logins) when the app starts Chrome with
              <code>--remote-debugging-port=9222</code>. Often works better than automation-only
              browsers for sites that block scripted logins; some sites may still restrict control.
            </span>
          </div>
          <label class="toggle">
            <input type="checkbox" id="br-use-user-chrome" ${browser.use_user_chrome ? 'checked' : ''}
                   onchange="document.getElementById('br-cdp-url-row').style.display=this.checked?'':'none'">
            <span class="slider"></span>
          </label>
        </div>
        <div id="br-cdp-url-row" class="wfield" style="margin-top:8px;${browser.use_user_chrome ? '' : 'display:none'}">
          <label>Chrome Debugging URL</label>
          <input type="text" id="br-cdp-url"
                 placeholder="http://localhost:9222"
                 value="${escHtml(browser.remote_debugging_url || 'http://localhost:9222')}">
          <div class="wfield-hint">
            Default <code>http://localhost:9222</code> — only change if you use a custom port.
            After you save settings, the <strong>first browser tool</strong> in a session connects
            here automatically (no need to type a command).
          </div>
          <details class="browser-cdp-guide">
            <summary>Step-by-step: connect your Chrome</summary>
            <ol class="browser-cdp-guide-steps">
              <li>
                Leave the URL as <code>http://localhost:9222</code> unless you deliberately run
                Chrome with another debugging port.
              </li>
              <li>
                <strong>Save</strong> these settings. Restart HushClaw if the app says a restart is required.
              </li>
              <li>
                <strong>Quit Chrome fully</strong> before the first connection
                (macOS: <kbd>Cmd</kbd>+<kbd>Q</kbd> on Chrome;
                Windows: close all windows and use &quot;Exit&quot; from the Chrome tray icon if it stays running).
                That releases the profile lock so HushClaw can start Chrome with debugging enabled while still using
                your <strong>default profile</strong> (same bookmarks, extensions, and saved logins as everyday use).
              </li>
              <li>
                Use the assistant as usual. The first time a browser tool runs, HushClaw tries to connect to that URL.
                If nothing is listening yet, it waits up to <strong>about 90 seconds</strong> for Chrome to finish
                quitting, then starts Chrome with <code>--remote-debugging-port=9222</code>.
                If you already started Chrome yourself with that flag, it connects immediately instead.
              </li>
              <li>
                Sign in on the site you need inside that Chrome window if prompted; then run browser actions again.
              </li>
              <li>
                <strong>Privacy:</strong> while remote debugging is on, other software on <em>this computer</em> could
                attach to the browser. Use only on a machine you trust.
              </li>
            </ol>
            <p class="browser-cdp-guide-foot wfield-hint">
              Official APIs and tokens (where a platform offers them) are still the most dependable for automation;
              use this mode when you need a real logged-in browser session.
            </p>
          </details>
        </div>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Skills Directories</h3>
      <div class="wfield">
        <label>User Skills Directory</label>
        <input type="text" id="sys-user-skill-dir"
               placeholder="Default: ~/Library/Application Support/hushclaw/user-skills"
               value="${escHtml(wizard.userSkillDir || '')}">
        <div class="wfield-hint">
          Skills you install via the UI or chat are stored here. Leave blank to use the default path.<br>
          System skills (deployed by install.sh): <code>${escHtml(wizard.systemSkillDir || "not configured")}</code>
        </div>
      </div>
      <div class="wfield">
        <label>Workspace Directory <span class="wfield-optional">(optional)</span></label>
        <input type="text" id="sys-workspace-dir"
               placeholder="Auto: .hushclaw/ in cwd"
               value="${escHtml(wizard.workspaceDir || '')}">
        <div class="wfield-hint">
          Per-project workspace. HushClaw reads <code>SOUL.md</code> (agent identity) and <code>USER.md</code> (user notes) from here.
          Auto-detected when a <code>.hushclaw/</code> folder exists in the current directory.
        </div>
      </div>
    </div>
    <div class="settings-section" id="ws-registry-section">
      <h3 class="settings-section-h">Workspace Registry <span class="wfield-optional" style="text-transform:none;letter-spacing:0;font-size:10.5px">(optional)</span></h3>
      <p class="wdesc">
        Named workspaces let you switch SOUL.md / USER.md / AGENTS.md / skills context per conversation without restarting.
        Create them here, then pick one from the sidebar before chatting.
      </p>
      <div id="ws-list">
        ${(wizard.workspacesList || []).map((ws, i) => `
        <div class="ws-entry-row" data-ws-idx="${i}">
          <div class="ws-entry-info">
            <span class="ws-entry-name">${escHtml(ws.name)}</span>
            <span class="ws-entry-path">${escHtml(ws.path)}</span>
            ${ws.description ? `<span class="ws-entry-desc">${escHtml(ws.description)}</span>` : ""}
          </div>
          <div class="ws-entry-actions">
            <button class="secondary small ws-move-btn" data-ws-idx="${i}" data-ws-dir="-1" title="Move up" ${i === 0 ? "disabled" : ""}>↑</button>
            <button class="secondary small ws-move-btn" data-ws-idx="${i}" data-ws-dir="1" title="Move down" ${i === (wizard.workspacesList || []).length - 1 ? "disabled" : ""}>↓</button>
            <button class="secondary small ws-init-btn" data-ws-idx="${i}" title="Create SOUL.md and USER.md for this workspace">Init Files</button>
            <button class="secondary small ws-remove-btn" data-ws-idx="${i}" title="Remove">✕</button>
          </div>
        </div>`).join("")}
        ${!(wizard.workspacesList || []).length ? `
          <div class="ws-list-empty">
            <div class="ws-list-empty-title">No workspaces configured yet.</div>
            <div class="ws-list-empty-body">Create one here to make it show up in the sidebar workspace strip.</div>
          </div>` : ""}
      </div>
      <div class="wfield-hint" id="ws-registry-status" style="margin-top:8px"></div>
      <div id="ws-add-row" class="ws-add-row hidden">
        <input type="text" id="ws-new-name" placeholder="name (e.g. project-alpha)" autocomplete="off" style="flex:0 0 140px">
        <input type="text" id="ws-new-path" placeholder="path (e.g. ~/workspace/alpha)" autocomplete="off" style="flex:1">
        <input type="text" id="ws-new-desc" placeholder="description (optional)" autocomplete="off" style="flex:1">
        <button id="ws-add-create">Create & Initialize</button>
        <button id="ws-add-confirm" class="secondary">Add Only</button>
        <button id="ws-add-cancel" class="secondary">✕</button>
      </div>
      <div style="margin-top:8px">
        <button id="ws-add-open" class="secondary small">+ New Workspace</button>
      </div>
    </div>
    <div class="settings-section">
      <h3 class="settings-section-h">Tool Profile</h3>
      <div class="wfield">
        <label>Profile preset</label>
        <select id="sys-tools-profile">
          <option value=""       ${wizard.toolsProfile === ""         ? "selected" : ""}>— Default (use enabled list) —</option>
          <option value="full"   ${wizard.toolsProfile === "full"     ? "selected" : ""}>full — all built-in tools</option>
          <option value="coding" ${wizard.toolsProfile === "coding"   ? "selected" : ""}>coding — file ops, shell, memory, todos</option>
          <option value="messaging" ${wizard.toolsProfile === "messaging" ? "selected" : ""}>messaging — email, calendar, memory</option>
          <option value="minimal"   ${wizard.toolsProfile === "minimal"   ? "selected" : ""}>minimal — remember, recall, get_time only</option>
        </select>
        <div class="wfield-hint">
          Restricts the tool set to a predefined profile. Applied before the enabled-list filter.
          Leave blank to rely solely on the <code>tools.enabled</code> list in your config.
        </div>
      </div>
    </div>
  `;
  bindThemeControls(els.wizardBody);

  const promptCustomizeBtn = document.getElementById("sys-system-prompt-customize");
  const promptResetBtn = document.getElementById("sys-system-prompt-reset");
  const promptEl = document.getElementById("sys-system-prompt");
  if (promptEl) {
    promptEl.addEventListener("input", () => {
      wizard.systemPrompt = promptEl.value;
      wizard.systemPromptDefault = false;
      wizard.systemPromptTouched = true;
    });
  }
  if (promptCustomizeBtn) {
    promptCustomizeBtn.addEventListener("click", () => {
      wizard.systemPromptDefault = false;
      wizard.systemPromptTouched = true;
      wizard.systemPrompt = "";
      renderSystemTab();
      document.getElementById("sys-system-prompt")?.focus();
    });
  }
  if (promptResetBtn) {
    promptResetBtn.addEventListener("click", () => {
      wizard.systemPrompt = "";
      wizard.systemPromptDefault = true;
      wizard.systemPromptTouched = true;
      renderSystemTab();
    });
  }

  document.querySelectorAll(".sys-api-key-input").forEach((el) => {
    const key = el.dataset.apiKey || "";
    if (!key) return;
    el.addEventListener("input", () => {
      const value = el.value.trim();
      if (value) wizard.apiKeyDrafts[key] = value;
      else delete wizard.apiKeyDrafts[key];
      delete wizard.apiKeyClears[key];
      const clearEl = document.querySelector(`.sys-api-key-clear[data-api-key="${CSS.escape(key)}"]`);
      if (clearEl) clearEl.checked = false;
    });
  });
  document.querySelectorAll(".sys-api-key-clear").forEach((el) => {
    const key = el.dataset.apiKey || "";
    if (!key) return;
    el.checked = Boolean(wizard.apiKeyClears[key]);
    el.addEventListener("change", () => {
      if (el.checked) {
        wizard.apiKeyClears[key] = true;
        delete wizard.apiKeyDrafts[key];
        const inputEl = document.querySelector(`.sys-api-key-input[data-api-key="${CSS.escape(key)}"]`);
        if (inputEl) inputEl.value = "";
      } else {
        delete wizard.apiKeyClears[key];
      }
    });
  });

  // ── Workspace Registry CRUD bindings ───────────────────────────────────────
  {
    const wsAddOpen   = document.getElementById("ws-add-open");
    const wsAddRow    = document.getElementById("ws-add-row");
    const wsAddCancel = document.getElementById("ws-add-cancel");
    const wsAddConfirm = document.getElementById("ws-add-confirm");
    const wsAddCreate = document.getElementById("ws-add-create");
    const wsStatus = document.getElementById("ws-registry-status");

    const resetAddForm = () => {
      ["ws-new-name", "ws-new-path", "ws-new-desc"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = "";
      });
    };

    const readWorkspaceDraft = () => {
      const name = (document.getElementById("ws-new-name")?.value || "").trim();
      const path = (document.getElementById("ws-new-path")?.value || "").trim();
      const desc = (document.getElementById("ws-new-desc")?.value || "").trim();
      return { name, path, description: desc };
    };

    const registerWorkspaceDraft = () => {
      const { name, path, description } = readWorkspaceDraft();
      if (!name || !path) {
        showToast("Name and path are required.", "err");
        return null;
      }
      if ((wizard.workspacesList || []).some(w => w.name === name)) {
        showToast(`Workspace "${name}" already exists.`, "err");
        return null;
      }
      wizard.workspacesList = [...(wizard.workspacesList || []), { name, path, description }];
      return { name, path, description };
    };

    if (wsAddOpen) wsAddOpen.addEventListener("click", () => {
      wsAddRow?.classList.remove("hidden");
      wsAddOpen.classList.add("hidden");
      if (wsStatus) wsStatus.textContent = "";
      document.getElementById("ws-new-name")?.focus();
    });
    if (wsAddCancel) wsAddCancel.addEventListener("click", () => {
      wsAddRow?.classList.add("hidden");
      wsAddOpen?.classList.remove("hidden");
      resetAddForm();
    });
    if (wsAddConfirm) wsAddConfirm.addEventListener("click", () => {
      const added = registerWorkspaceDraft();
      if (!added) return;
      if (wsStatus) wsStatus.textContent = `Added "${added.name}". Click Save to persist it.`;
      renderSystemTab();
    });

    if (wsAddCreate) wsAddCreate.addEventListener("click", () => {
      const added = registerWorkspaceDraft();
      if (!added) return;
      if (wsStatus) wsStatus.textContent = `Creating files for "${added.name}"…`;
      renderSystemTab();
      const statusEl = document.getElementById("ws-registry-status");
      if (statusEl) statusEl.textContent = `Creating files for "${added.name}"…`;
      const createBtn = document.getElementById("ws-add-create");
      if (createBtn) createBtn.disabled = true;
      showToast(`Workspace "${added.name}" added. Click Save to persist it.`, "ok");
      syncFormToState();
      showToast(`Initializing "${added.name}"…`, "info");
      window.setTimeout(() => {
        const btn = document.getElementById("ws-add-open");
        if (btn) btn.classList.remove("hidden");
        const row = document.getElementById("ws-add-row");
        if (row) row.classList.add("hidden");
      }, 0);
      send({ type: "init_workspace", path: added.path });
    });

    document.querySelectorAll(".ws-remove-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.wsIdx);
        wizard.workspacesList = (wizard.workspacesList || []).filter((_, i) => i !== idx);
        if (wsStatus) wsStatus.textContent = "Workspace removed from the registry. Click Save to persist this change.";
        renderSystemTab();
      });
    });

    document.querySelectorAll(".ws-move-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.wsIdx);
        const dir = Number(btn.dataset.wsDir);
        const list = [...(wizard.workspacesList || [])];
        const next = idx + dir;
        if (!Number.isInteger(idx) || !Number.isInteger(next) || next < 0 || next >= list.length) return;
        [list[idx], list[next]] = [list[next], list[idx]];
        wizard.workspacesList = list;
        if (wsStatus) wsStatus.textContent = "Workspace order changed. Click Save to persist it.";
        renderSystemTab();
      });
    });

    document.querySelectorAll(".ws-init-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.wsIdx);
        const item = (wizard.workspacesList || [])[idx];
        if (!item?.path) {
          showToast("Workspace path is missing.", "err");
          return;
        }
        btn.disabled = true;
        if (wsStatus) wsStatus.textContent = `Initializing "${item.name}"…`;
        send({ type: "init_workspace", path: item.path });
      });
    });
  }

  const devModeChk = document.getElementById("sys-dev-mode");
  if (devModeChk) {
    devModeChk.addEventListener("change", () => {
      try { localStorage.setItem("hushclaw.dev.mode", devModeChk.checked ? "1" : "0"); } catch { /* ignore */ }
      _syncForceBtnVisibility();
      refreshNavUpdateAction();
    });
  }

  const checkBtn = document.getElementById("upd-check-btn");
  const upgradeBtn = document.getElementById("upd-upgrade-btn");
  const forceBtn = document.getElementById("upd-force-btn");

  function _syncForceBtnVisibility() {
    if (!forceBtn) return;
    const devOn = (() => { try { return localStorage.getItem("hushclaw.dev.mode") === "1"; } catch { return false; } })();
    forceBtn.style.display = devOn ? "" : "none";
  }
  _syncForceBtnVisibility();

  if (checkBtn) {
    checkBtn.addEventListener("click", () => {
      syncFormToState();
      requestCheckUpdate(true);
    });
  }
  if (upgradeBtn) {
    upgradeBtn.addEventListener("click", () => {
      syncFormToState();
      confirmAndRunUpdate();
    });
  }
  if (forceBtn) {
    forceBtn.addEventListener("click", () => {
      syncFormToState();
      confirmAndForceUpgrade();
    });
  }
  refreshUpdateUi();
}

const el = Object.fromEntries([...document.querySelectorAll("[id]")].map(node => [node.id, node]));
const names = { openai: "OpenAI", deepseek: "DeepSeek", legacy: "服务器原配置" };
let profiles = {};
let revision = 0;
let timer;
let busy = false;

async function api(path, body, method = body === undefined ? "GET" : "POST") {
  const options = { method };
  if (method !== "GET") {
    options.headers = { "Content-Type": "application/json", "X-Admin-Request": "1" };
    options.body = JSON.stringify(body);
  }
  const r = await fetch("/api/admin/" + path, { ...options, cache: "no-store" });
  if (r.status === 401) { location.replace("/"); throw Error("请重新登录。"); }
  if (r.status === 403) { location.replace("/app"); throw Error("需要管理员权限。"); }
  const data = await r.json();
  if (!r.ok) throw Error(data.detail || "请求失败，请重试。");
  return data;
}

function action(text, className, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  button.addEventListener("click", onClick);
  return button;
}

function showSettings(data) {
  profiles = data.profiles;
  el.activeModel.textContent = data.active === "legacy" && !data.legacyConfigured
    ? "当前尚未配置解析模型。" : `当前使用 ${names[data.active]} · ${data.model}`;
  el.savedModels.replaceChildren();
  for (const [provider, profile] of Object.entries(profiles)) {
    const section = document.createElement("section");
    const header = document.createElement("div");
    header.className = "provider-heading";
    const title = document.createElement("strong");
    title.textContent = names[provider];
    header.append(title, action("删除密钥", "ghost", () => {
      const fallback = data.legacyConfigured ? "新解析将使用服务器原配置。" : "重新启用模型前，新解析将暂停。";
      if (confirm(`从本站删除 ${names[provider]} 的 API Key 及其已保存模型？${data.active === provider ? fallback : ""}`)) {
        updateSettings(() => api("settings/" + provider, undefined, "DELETE"), "已删除该供应商的密钥和模型。");
      }
    }));
    section.append(header);
    for (const model of profile.models) {
      const row = document.createElement("div");
      row.className = "saved-model";
      const label = document.createElement("span");
      label.textContent = model;
      row.append(label);
      if (provider === data.active && model === data.model) {
        const status = document.createElement("span");
        status.textContent = "使用中";
        row.append(status);
      } else {
        row.append(action("启用", "secondary", () => updateSettings(
          () => api("settings/activate", { provider, model }), `已启用 ${names[provider]} · ${model}。`)));
      }
      section.append(row);
    }
    el.savedModels.append(section);
  }
  if (!el.savedModels.childElementCount) el.savedModels.textContent = "尚未保存模型。";
}

async function updateSettings(request, message) {
  if (busy) return;
  busy = true;
  el.savedModels.inert = true;
  el.settings.disabled = true;
  clearTimeout(timer);
  revision++;
  el.configStatus.textContent = "正在更新配置…";
  try {
    const data = await request();
    showSettings(data);
    el.provider.value = data.active === "legacy" ? "openai" : data.active;
    el.editor.open = !Object.keys(profiles).length;
    changeProvider();
    el.configStatus.textContent = message;
  } catch (e) { el.configStatus.textContent = e.message; }
  finally { busy = false; el.savedModels.inert = false; el.settings.disabled = false; }
}

function credentials() {
  return { provider: el.provider.value, api_key: el.apiKey.value.trim() || null };
}

function resetModels() {
  clearTimeout(timer);
  revision++;
  el.model.replaceChildren(new Option("请先获取模型列表", ""));
  el.model.disabled = el.save.disabled = true;
}

async function loadModels() {
  resetModels();
  const version = revision;
  const payload = credentials();
  if (!payload.api_key && !profiles[payload.provider]) {
    el.configStatus.textContent = "请输入该厂商的 API Key。";
    return;
  }
  el.configStatus.textContent = "正在获取模型列表…";
  try {
    const { models } = await api("models", payload);
    if (version !== revision) return;
    el.model.replaceChildren(new Option("请选择模型", ""), ...models.map(m => new Option(m, m)));
    const previous = profiles[payload.provider]?.model;
    if (models.includes(previous)) el.model.value = previous;
    el.model.disabled = !models.length;
    el.save.disabled = !el.model.value;
    el.configStatus.textContent = models.length ? `已获取 ${models.length} 个模型。` : "该密钥没有可选的文本模型。";
  } catch (e) { if (version === revision) el.configStatus.textContent = e.message; }
}

function changeProvider() {
  resetModels();
  el.apiKey.value = "";
  el.configStatus.textContent = "";
  const saved = Boolean(profiles[el.provider.value]);
  el.keyHint.textContent = saved ? "已保存密钥。留空沿用，输入新密钥可替换。" : "输入密钥后自动获取可选模型。";
  if (saved && el.editor.open) loadModels();
}

el.editor.addEventListener("toggle", () => {
  if (el.editor.open && !el.settings.disabled && !el.apiKey.value && profiles[el.provider.value]) loadModels();
});
el.provider.addEventListener("change", changeProvider);
el.apiKey.addEventListener("input", () => {
  resetModels();
  el.configStatus.textContent = "";
  if (el.apiKey.value.trim() || profiles[el.provider.value]) timer = setTimeout(loadModels, 750);
});
el.refresh.addEventListener("click", loadModels);
el.model.addEventListener("change", () => { el.save.disabled = !el.model.value; });
el.save.addEventListener("click", () => {
  const payload = { ...credentials(), model: el.model.value };
  updateSettings(() => api("settings", payload), "已保存并启用模型。");
});

el.invite.addEventListener("click", async () => {
  el.invite.disabled = true;
  el.inviteStatus.textContent = "";
  try {
    const data = await api("invites", {});
    el.code.value = data.code;
    el.inviteResult.hidden = false;
    el.inviteStatus.textContent = "已生成。";
  } catch (e) { el.inviteStatus.textContent = e.message; }
  finally { el.invite.disabled = false; }
});
el.copy.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(el.code.value); el.inviteStatus.textContent = "已复制。"; }
  catch { el.code.focus(); el.code.select(); el.inviteStatus.textContent = "已选中邀请码，请手动复制。"; }
});
window.addEventListener("pagehide", () => {
  resetModels();
  el.apiKey.value = el.code.value = "";
  el.inviteResult.hidden = true;
});
window.addEventListener("pageshow", async () => {
  try {
    const data = await api("settings");
    showSettings(data);
    el.provider.value = data.active === "legacy" ? "openai" : data.active;
    el.settings.disabled = false;
    el.editor.open = !Object.keys(profiles).length;
    changeProvider();
  } catch (e) { el.activeModel.textContent = e.message; }
});

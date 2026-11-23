const el = Object.fromEntries([...document.querySelectorAll("[id]")].map(node => [node.id, node]));
const names = { openai: "OpenAI", deepseek: "DeepSeek", legacy: "服务器原配置" };
let profiles = {};
let revision = 0;
let timer;

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Admin-Request": "1" },
    body: JSON.stringify(body),
  };
  const r = await fetch("/api/admin/" + path, { ...options, cache: "no-store" });
  if (r.status === 401) { location.replace("/"); throw Error("请重新登录。"); }
  if (r.status === 403) { location.replace("/app"); throw Error("需要管理员权限。"); }
  const data = await r.json();
  if (!r.ok) throw Error(data.detail || "请求失败，请重试。");
  return data;
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
  if (saved) loadModels();
}

el.provider.addEventListener("change", changeProvider);
el.apiKey.addEventListener("input", () => {
  resetModels();
  el.configStatus.textContent = "";
  if (el.apiKey.value.trim() || profiles[el.provider.value]) timer = setTimeout(loadModels, 750);
});
el.refresh.addEventListener("click", loadModels);
el.model.addEventListener("change", () => { el.save.disabled = !el.model.value; });
el.save.addEventListener("click", async () => {
  const payload = { ...credentials(), model: el.model.value };
  el.settings.disabled = true;
  el.configStatus.textContent = "正在验证并保存…";
  try {
    const data = await api("settings", payload);
    profiles[data.active] = { model: data.model };
    el.apiKey.value = "";
    el.keyHint.textContent = "已保存密钥。留空沿用，输入新密钥可替换。";
    el.activeModel.textContent = `当前使用 ${names[data.active]} · ${data.model}`;
    el.configStatus.textContent = "已启用，新解析将使用所选模型。";
  } catch (e) { el.configStatus.textContent = e.message; }
  finally { el.settings.disabled = false; }
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
    profiles = data.profiles;
    el.provider.value = data.active === "legacy" ? "openai" : data.active;
    el.activeModel.textContent = `当前使用 ${names[data.active]} · ${data.model}`;
    el.settings.disabled = false;
    changeProvider();
  } catch (e) { el.activeModel.textContent = e.message; }
});

const STORAGE_KEY = "hamExamProgressV1";

const bankFiles = { A: "/questions.json", B: "/questions-B.json", C: "/questions-C.json" };
let switchVersion = 0;
let pendingSave = Promise.resolve();

const state = {
  bank: null,
  scoped: [],
  payload: null,
  questions: [],
  filtered: [],
  currentIndex: 0,
  mode: "practice",
  submitted: false,
  exam: null,
  currentUser: null,
  pendingExplanations: new Set(),
  precacheTimer: null,
  progress: {
    answers: {},
    wrong: [],
  },
};

const els = {};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindElements();
  bindEvents();

  try {
    state.currentUser = await fetchCurrentUser();
    if (!state.currentUser) {
      location.replace("/");
      return;
    }
    els.currentUserLabel.textContent = state.currentUser.username;
    document.getElementById("adminLink").hidden = !state.currentUser.isAdmin;
    els.startPrecacheBtn.hidden = !state.currentUser.isAdmin;
    els.bankSelect.disabled = false;
    const saved = localStorage.getItem(bankPreferenceKey());
    await switchBank(Object.hasOwn(bankFiles, saved) ? saved : "A");
    await refreshPrecacheStatus();
    state.precacheTimer = setInterval(refreshPrecacheStatus, 10000);
  } catch (error) {
    document.getElementById("subtitle").textContent = "题库载入失败，请刷新重试。";
    console.error(error);
  }
}

function bankPreferenceKey() {
  return "hamExamBank:" + state.currentUser.username;
}

function setLearningControlsBusy(busy) {
  document.querySelectorAll(".toolbar, .view").forEach(el => { el.inert = busy; });
}

async function switchBank(bank) {
  if (!state.currentUser) return;
  const version = ++switchVersion;
  els.bankSelect.value = bank;
  els.bankStatus.textContent = "";
  if (bank === state.bank || (state.exam && !state.exam.submitted &&
      !window.confirm("切换题库将结束当前未交卷的考试，确定切换吗？"))) {
    els.bankSelect.value = state.bank;
    setLearningControlsBusy(false);
    return;
  }
  setLearningControlsBusy(true);
  els.bankStatus.textContent = `正在载入 ${bank} 类…`;
  try {
    await pendingSave;
    const [payload, progress] = await Promise.all([
      fetchJson(bankFiles[bank]), fetchJson(`/api/progress?bank=${bank}`),
    ]);
    if (version !== switchVersion) return;
    state.bank = bank;
    state.payload = payload;
    state.questions = payload.questions;
    state.progress = progress;
    state.exam = null;
    els.examSetup.hidden = false;
    els.examPaper.hidden = els.examResult.hidden = true;
    els.examPaper.replaceChildren();
    els.examResult.replaceChildren();
    els.jumpDialog.close();
    els.aiDialog.close();
    els.orderSelect.value = "sequential";
    els.scopeSelect.replaceChildren(new Option(`全部 ${bank} 类题（${payload.count}）`, "all"));
    if (bank !== "A") els.scopeSelect.add(new Option(`${bank} 类新增题（${payload.upgradeIds.length}）`, "upgrade"));
    updateScope();
    els.resetProgressBtn.textContent = `清空 ${bank} 类进度`;
    els.clearWrongBtn.textContent = `清空 ${bank} 类错题`;
    renderAll();
    els.bankStatus.textContent = "";
    localStorage.setItem(bankPreferenceKey(), bank);
  } catch (error) {
    if (version === switchVersion) els.bankStatus.textContent = `${bank} 类载入失败，点击类别重试。`;
  } finally {
    if (version === switchVersion) {
      els.bankSelect.value = state.bank || "";
      setLearningControlsBusy(!state.bank);
    }
  }
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function fetchCurrentUser() {
  try {
    const response = await fetch("/api/me");
    if (!response.ok) return null;
    return response.json();
  } catch {
    return null;
  }
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  // 退出登录，重定向回登录页
  window.location.href = "/";
}


function bindElements() {
  [
    "bankSelect", "scopeSelect", "scopeLabel", "scopeField", "bankStatus", "practiceFigure",
    "subtitle", "statAnswered", "statAccuracy", "statWrong", "categorySelect", "orderSelect",
    "resetProgressBtn", "questionCounter", "questionType", "questionCategory", "questionText", "questionTextContent",
    "options", "feedback", "prevBtn", "submitBtn", "nextBtn", "wrongList", "clearWrongBtn",
    "examCount", "startExamBtn", "examSetup", "examPaper", "examResult", "categoryStats",
    "jumpDialog", "jumpTitle", "jumpHint", "jumpGrid", "closeJumpBtn",
    "appShell", "currentUserLabel", "logoutBtn", "generateAiBtn", "regenerateAiBtn",
    "openAiBtn", "aiDialog", "aiQuestionInfo", "aiCacheInfo", "aiStatus", "aiContent", "cacheToolStatus", "startPrecacheBtn",
  ].forEach((id) => {
    els[id] = document.getElementById(id);
  });
}

function bindEvents() {
  setLearningControlsBusy(true);
  els.bankSelect.addEventListener("change", () => switchBank(els.bankSelect.value));
  els.scopeSelect.addEventListener("change", () => {
    updateScope();
    renderPractice();
  });
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  });
  els.categorySelect.addEventListener("change", () => {
    applyFilter();
    renderPractice();
    renderStats();
  });
  els.orderSelect.addEventListener("change", () => {
    applyFilter();
    renderPractice();
  });
  els.prevBtn.addEventListener("click", () => moveQuestion(-1));
  els.nextBtn.addEventListener("click", () => moveQuestion(1));
  els.submitBtn.addEventListener("click", submitPractice);
  els.clearWrongBtn.addEventListener("click", clearWrong);
  els.resetProgressBtn.addEventListener("click", resetProgress);
  els.startExamBtn.addEventListener("click", startExam);
  els.questionCounter.addEventListener("click", openQuestionJump);
  els.questionCategory.addEventListener("click", openCategoryJump);
  els.closeJumpBtn.addEventListener("click", closeJump);
  els.logoutBtn.addEventListener("click", logout);
  els.openAiBtn.addEventListener("click", () => {
    els.openAiBtn.focus({ preventScroll: true });
    els.aiQuestionInfo.textContent = els.questionCounter.textContent + " · " + els.questionCategory.textContent;
    els.aiDialog.showModal();
    if (els.aiDialog.dataset.question !== state.filtered[state.currentIndex].type) {
      els.aiDialog.dataset.question = state.filtered[state.currentIndex].type;
      els.aiDialog.querySelector(".ai-reader").scrollTop = 0;
    }
    generateAiExplanation(false);
  });
  els.aiDialog.addEventListener("click", (event) => {
    if (event.target !== els.aiDialog) return;
    const bounds = els.aiDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom) els.aiDialog.close();
  });
  els.generateAiBtn.addEventListener("click", () => generateAiExplanation(false));
  els.regenerateAiBtn.addEventListener("click", () => generateAiExplanation(true));
  els.startPrecacheBtn.addEventListener("click", startPrecache);
}

function syncScopeLabel() {
  els.scopeField.hidden = state.bank === "A" || state.mode !== "practice";
  els.scopeLabel.hidden = els.scopeField.hidden || els.scopeSelect.value !== "upgrade";
  els.scopeLabel.textContent = `${state.bank} 类新增题`;
}

function updateScope() {
  const ids = new Set(state.payload.upgradeIds);
  state.scoped = els.scopeSelect.value === "upgrade"
    ? state.questions.filter(question => ids.has(question.type))
    : state.questions;
  syncScopeLabel();
  els.categorySelect.innerHTML = `<option value="all">全部知识点</option>`;
  [...new Set(state.scoped.map(q => q.category))].forEach((category) => {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = topicLabel(category);
    els.categorySelect.append(option);
  });
  applyFilter();
}

function loadProgress() {
  const saved = loadJson(STORAGE_KEY, null);
  if (saved && saved.answers && Array.isArray(saved.wrong)) return saved;
  return { answers: {}, wrong: [] };
}

function saveProgress() {
  const bank = state.bank;
  const body = JSON.stringify(state.progress);
  pendingSave = pendingSave.then(async () => {
    const response = await fetch(`/api/progress?bank=${bank}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body,
    });
    if (!response.ok) throw new Error("保存失败");
  }).catch(() => { els.bankStatus.textContent = `${bank} 类学习进度保存失败。`; });
}

function loadJson(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch {
    localStorage.removeItem(key);
    return fallback;
  }
}

function applyFilter() {
  const category = els.categorySelect.value;
  state.filtered = category === "all"
    ? [...state.scoped]
    : state.scoped.filter((question) => question.category === category);
  if (els.orderSelect.value === "random") {
    shuffle(state.filtered);
  }
  state.currentIndex = 0;
  state.submitted = false;
}

function setMode(mode) {
  state.mode = mode;
  syncScopeLabel();
  document.querySelectorAll(".tab").forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.getElementById(`${mode}View`).classList.add("active");
  renderAll();
}

function renderAll() {
  renderHeader();
  renderPractice();
  renderWrongList();
  renderStats();
}


function renderHeader() {
  const answered = Object.keys(state.progress.answers).length;
  const correct = Object.values(state.progress.answers).filter((item) => item.correct).length;
  els.subtitle.textContent = `${state.questions.length} 道题 · ${state.payload.categories.length} 个知识点`;
  els.statAnswered.textContent = answered;
  els.statAccuracy.textContent = answered ? `${Math.round((correct / answered) * 100)}%` : "0%";
  els.statWrong.textContent = state.progress.wrong.length;
}

function figureHtml(question) {
  return question?.figure
    ? `<img class="question-figure" src="/figures/${encodeURIComponent(question.figure)}" alt="题目 ${escapeHtml(question.sourceId)} 附图">`
    : "";
}

function renderPractice() {
  const question = state.filtered[state.currentIndex];
  els.practiceFigure.innerHTML = figureHtml(question);
  els.openAiBtn.disabled = !question;
  if (!question) {
    els.questionCounter.textContent = "第 0 / 0 题";
    els.questionType.textContent = "";
    els.questionTextContent.textContent = "没有符合筛选条件的题目";
    els.options.innerHTML = "";
    return;
  }

  state.submitted = false;
  els.questionCounter.textContent = `第 ${state.currentIndex + 1} / ${state.filtered.length} 题`;
  els.questionType.textContent = question.multi ? "多选" : "单选";
  els.questionCategory.textContent = `知识点 ${question.category}`;
  els.questionTextContent.textContent = question.question;
  els.feedback.hidden = true;
  els.feedback.className = "feedback";
  els.options.innerHTML = "";

  Object.entries(question.options).forEach(([key, text]) => {
    const label = document.createElement("label");
    label.className = "option";
    label.innerHTML = `
      <input name="practiceOption" type="${question.multi ? "checkbox" : "radio"}" value="${key}">
      <strong class="option-key">${key}</strong><span>${escapeHtml(text)}</span>
    `;
    els.options.append(label);
  });

  els.prevBtn.disabled = state.currentIndex === 0;
  els.nextBtn.disabled = state.currentIndex >= state.filtered.length - 1;
}

function submitPractice() {
  const question = state.filtered[state.currentIndex];
  if (!question) return;
  const selected = selectedPracticeAnswers();
  if (!selected.length) {
    els.feedback.hidden = false;
    els.feedback.className = "feedback error";
    els.feedback.textContent = "请先选择答案。";
    return;
  }

  const correct = sameAnswers(selected, question.answer);
  recordAnswer(question, selected, correct);
  saveProgress();
  renderHeader();
  renderWrongList();
  renderStats();

  if (correct && state.currentIndex < state.filtered.length - 1) {
    state.currentIndex += 1;
    renderPractice();
    els.feedback.hidden = false;
    els.feedback.className = "feedback success";
    els.feedback.textContent = "上一题回答正确，已进入下一题。";
    return;
  }

  markOptions(question, selected);
  showFeedback(correct, correct ? "回答正确。" : "回答错误。", question);
}

function selectedPracticeAnswers() {
  return [...els.options.querySelectorAll("input:checked")].map((input) => input.value).sort();
}

function recordAnswer(question, selected, correct) {
  state.progress.answers[question.type] = {
    correct,
    selected,
    at: new Date().toISOString(),
    category: question.category,
  };
  const wrongSet = new Set(state.progress.wrong);
  if (correct) {
    wrongSet.delete(question.type);
  } else {
    wrongSet.add(question.type);
  }
  state.progress.wrong = [...wrongSet];
}

function markOptions(question, selected) {
  els.options.querySelectorAll(".option").forEach((label) => {
    const value = label.querySelector("input").value;
    label.classList.toggle("correct", question.answer.includes(value));
    label.classList.toggle("wrong", selected.includes(value) && !question.answer.includes(value));
  });
}

function showFeedback(correct, message, question) {
  els.feedback.hidden = false;
  els.feedback.className = `feedback ${correct ? "success" : "error"}`;
  els.feedback.textContent = `${message} 正确答案：${question.answer.join("")}`;
}

function showAiExplanation(result) {
  els.aiContent.innerHTML = result ? formatAiText(result.content) : "";
  const date = result?.generatedAt ? new Date(result.generatedAt).toLocaleString() : "";
  els.aiCacheInfo.textContent = [result?.model, date].filter(Boolean).join(" · ");
}

function clearAiStatus() {
  els.aiStatus.hidden = true;
  els.aiStatus.className = "ai-status";
  els.aiStatus.textContent = "";
}

function setAiStatus(message, type = "") {
  els.aiStatus.hidden = false;
  els.aiStatus.className = `ai-status ${type}`;
  els.aiStatus.textContent = message;
}

async function parseJsonResponse(response) {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return { detail: text };
  }
}

async function generateAiExplanation(force) {
  const question = state.filtered[state.currentIndex];
  if (!question) return;

  const key = question.type;
  clearAiStatus();
  els.generateAiBtn.hidden = true;
  if (!force) {
    els.regenerateAiBtn.hidden = true;
    showAiExplanation(null);
  }

  setAiStatus("正在读取或生成本题解析…", "loading");
  els.regenerateAiBtn.disabled = true;
  if (state.pendingExplanations.has(key)) return;
  state.pendingExplanations.add(key);
  try {
    const result = await callLlm(question, force);
    if (state.filtered[state.currentIndex]?.type !== key) return;
    clearAiStatus();
    showAiExplanation(result);
    els.regenerateAiBtn.hidden = false;
  } catch (error) {
    if (state.filtered[state.currentIndex]?.type !== key) return;
    setAiStatus(llmErrorMessage(error), "error");
    els.generateAiBtn.hidden = false;
  } finally {
    state.pendingExplanations.delete(key);
    if (state.filtered[state.currentIndex]?.type === key) els.regenerateAiBtn.disabled = false;
  }
}

async function callLlm(question, force) {
  const response = await fetch("/api/llm/explain", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question_type: question.type,
      force,
    }),
  });

  const data = await parseJsonResponse(response);

  if (!response.ok) {
    const detail = data?.detail || data?.error?.message || `${response.status} ${response.statusText}`;
    throw new Error(`HTTP ${response.status}: ${detail}`);
  }

  if (!data?.content) {
    throw new Error("接口返回中没有解析内容。");
  }
  return data;
}

function llmErrorMessage(error) {
  const message = error?.message || String(error);
  if (message.includes("HTTP 401")) {
    return "请求失败：请先登录。";
  }
  if (message.includes("Failed to fetch") || message.includes("NetworkError")) {
    return "请求失败。请确认后端服务正在运行。";
  }
  return `请求失败：${message}`;
}

function formatAiText(text) {
  const lines = escapeHtml(text).split(/\r?\n/);
  const html = [];
  let paragraph = [];
  let listType = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${paragraph.map(formatInlineMarkdown).join("<br>")}</p>`);
    paragraph = [];
  };

  const closeList = () => {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = null;
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      closeList();
      return;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph();
      closeList();
      const level = Math.min(heading[1].length + 2, 6);
      html.push(`<h${level}>${formatInlineMarkdown(heading[2])}</h${level}>`);
      return;
    }

    const unordered = /^[-*]\s+(.+)$/.exec(trimmed);
    const ordered = /^\d+[.)]\s+(.+)$/.exec(trimmed);
    const item = unordered || ordered;
    if (item) {
      flushParagraph();
      const nextListType = unordered ? "ul" : "ol";
      if (listType !== nextListType) {
        closeList();
        html.push(`<${nextListType}>`);
        listType = nextListType;
      }
      html.push(`<li>${formatInlineMarkdown(item[1])}</li>`);
      return;
    }

    closeList();
    paragraph.push(trimmed);
  });

  flushParagraph();
  closeList();
  return html.join("");
}

function formatInlineMarkdown(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function setCacheToolStatus(message, type = "") {
  els.cacheToolStatus.className = type;
  els.cacheToolStatus.textContent = message;
}

async function refreshPrecacheStatus() {
  try {
    const response = await fetch("/api/llm/precache");
    const status = await parseJsonResponse(response);
    if (!response.ok) throw new Error(status?.detail || `${response.status} ${response.statusText}`);
    renderPrecacheStatus(status);
  } catch (error) {
    setCacheToolStatus(`后台预缓存状态获取失败：${error.message}`, "error");
  }
}

async function startPrecache() {
  try {
    els.startPrecacheBtn.disabled = true;
    setCacheToolStatus("正在启动后台预生成任务...", "loading");
    const response = await fetch("/api/llm/precache", { method: "POST", headers: { "X-Admin-Request": "1" } });
    const status = await parseJsonResponse(response);
    if (!response.ok) throw new Error(status?.detail || `${response.status} ${response.statusText}`);
    renderPrecacheStatus(status);
  } catch (error) {
    setCacheToolStatus(`后台预生成启动失败：${error.message}`, "error");
    els.startPrecacheBtn.disabled = false;
  }
}

function renderPrecacheStatus(status) {
  const total = status.total || 0;
  const cached = status.cached || 0;
  if (!status.configured) {
    setCacheToolStatus(`AI 解析缓存 ${cached}/${total}。未配置 LLM_API_KEY，后台预生成未启动。`, "error");
    els.startPrecacheBtn.disabled = true;
    return;
  }
  if (status.running) {
    const current = status.current ? `，当前 ${status.current}` : "";
    setCacheToolStatus(`后台预生成中：已缓存 ${cached}/${total}，本轮生成 ${status.generated}、跳过 ${status.skipped}、失败 ${status.failed}${current}。`, "loading");
    els.startPrecacheBtn.disabled = true;
    return;
  }
  if (cached >= total) {
    setCacheToolStatus(`AI 解析已全部缓存：${cached}/${total}。`, "success");
    els.startPrecacheBtn.disabled = true;
    return;
  }
  const errorText = status.last_error ? `上次错误：${status.last_error}` : "可继续后台预生成。";
  setCacheToolStatus(`AI 解析缓存 ${cached}/${total}，剩余 ${status.remaining}。${errorText}`, status.failed ? "error" : "");
  els.startPrecacheBtn.disabled = false;
}

function moveQuestion(offset) {
  const nextIndex = state.currentIndex + offset;
  if (nextIndex < 0 || nextIndex >= state.filtered.length) return;
  state.currentIndex = nextIndex;
  renderPractice();
}

function openQuestionJump() {
  if (!state.filtered.length) return;
  els.jumpTitle.textContent = "选择题目";
  els.jumpHint.textContent = "绿色：已做对；红色：已做错；灰色：未作答。";
  els.jumpGrid.className = "jump-grid question-jump-grid";
  els.jumpGrid.innerHTML = "";
  state.filtered.forEach((question, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `jump-card ${questionStatus(question)}${index === state.currentIndex ? " current" : ""}`;
    button.innerHTML = `<strong>${index + 1}</strong><span>${question.category}</span>`;
    button.addEventListener("click", () => {
      state.currentIndex = index;
      closeJump();
      renderPractice();
    });
    els.jumpGrid.append(button);
  });
  openJump();
}

function openCategoryJump() {
  els.jumpTitle.textContent = "选择知识点";
  els.jumpHint.textContent = "进入知识点后从该分类第一题开始练习。";
  els.jumpGrid.className = "jump-grid category-jump-grid";
  els.jumpGrid.innerHTML = "";

  const allButton = document.createElement("button");
  allButton.type = "button";
  allButton.className = "jump-card category-card-button";
  allButton.innerHTML = `<strong>全部知识点</strong><span>${state.scoped.length} 题</span>`;
  allButton.addEventListener("click", () => jumpToCategory("all"));
  els.jumpGrid.append(allButton);

  [...els.categorySelect.options].slice(1).forEach(({ value: category }) => {
    const questions = state.scoped.filter((question) => question.category === category);
    const answered = questions.filter((question) => state.progress.answers[question.type]).length;
    const wrong = questions.filter((question) => state.progress.answers[question.type] && !state.progress.answers[question.type].correct).length;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `jump-card category-card-button ${wrong ? "status-wrong" : answered ? "status-correct" : "status-pending"}`;
    button.innerHTML = `<strong>${escapeHtml(topicLabel(category))}</strong><span>${answered}/${questions.length} 已答</span>`;
    button.addEventListener("click", () => jumpToCategory(category));
    els.jumpGrid.append(button);
  });
  openJump();
}

function jumpToCategory(category) {
  els.categorySelect.value = category;
  els.orderSelect.value = "sequential";
  applyFilter();
  closeJump();
  setMode("practice");
}

function openJump() {
  if (typeof els.jumpDialog.showModal === "function") {
    els.jumpDialog.showModal();
  } else {
    els.jumpDialog.setAttribute("open", "");
  }
}

function closeJump() {
  if (typeof els.jumpDialog.close === "function") {
    els.jumpDialog.close();
  } else {
    els.jumpDialog.removeAttribute("open");
  }
}

function questionStatus(question) {
  const answer = state.progress.answers[question.type];
  if (!answer) return "status-pending";
  return answer.correct ? "status-correct" : "status-wrong";
}

function renderWrongList() {
  const wrongQuestions = state.progress.wrong
    .map((id) => state.questions.find((question) => question.type === id))
    .filter(Boolean);

  if (!wrongQuestions.length) {
    els.wrongList.innerHTML = `<p class="empty">暂无错题。</p>`;
    return;
  }

  els.wrongList.innerHTML = "";
  wrongQuestions.forEach((question) => {
    const item = document.createElement("article");
    item.className = "list-item";
    item.innerHTML = `
      <h3>${escapeHtml(question.question)}</h3>
      ${figureHtml(question)}
      <p>知识点 ${question.category} · 正确答案 ${question.answer.join("")}</p>
      <button class="secondary" type="button">重刷这题</button>
    `;
    item.querySelector("button").addEventListener("click", () => jumpToQuestion(question));
    els.wrongList.append(item);
  });
}

function jumpToQuestion(question) {
  if (!state.scoped.includes(question)) {
    els.scopeSelect.value = "all";
    updateScope();
  }
  els.categorySelect.value = "all";
  els.orderSelect.value = "sequential";
  applyFilter();
  state.currentIndex = state.filtered.findIndex((item) => item.type === question.type);
  setMode("practice");
  renderPractice();
}

function clearWrong() {
  state.progress.wrong = [];
  saveProgress();
  renderAll();
}

function resetProgress() {
  state.progress = { answers: {}, wrong: [] };
  saveProgress();
  renderAll();
}

function startExam() {
  const count = Math.min(Math.max(Number(els.examCount.value) || 30, 5), state.questions.length);
  const paper = [...state.questions];
  shuffle(paper);
  state.exam = {
    questions: paper.slice(0, count),
    submitted: false,
  };
  els.examSetup.hidden = true;
  els.examResult.hidden = true;
  els.examPaper.hidden = false;
  renderExamPaper();
}

function renderExamPaper() {
  els.examPaper.innerHTML = "";
  state.exam.questions.forEach((question, index) => {
    const article = document.createElement("article");
    article.className = "exam-question";
    article.innerHTML = `
      <h3>${index + 1}. ${escapeHtml(question.question)}</h3>
      ${figureHtml(question)}
      <div class="exam-options">
        ${Object.entries(question.options).map(([key, text]) => `
          <label class="option">
            <input name="exam-${index}" type="${question.multi ? "checkbox" : "radio"}" value="${key}">
            <strong class="option-key">${key}</strong><span>${escapeHtml(text)}</span>
          </label>
        `).join("")}
      </div>
    `;
    els.examPaper.append(article);
  });
  const submit = document.createElement("button");
  submit.className = "exam-submit";
  submit.type = "button";
  submit.textContent = "提交试卷";
  submit.addEventListener("click", submitExam);
  els.examPaper.append(submit);
}

function submitExam() {
  const rows = state.exam.questions.map((question, index) => {
    const selected = [...els.examPaper.querySelectorAll(`input[name="exam-${index}"]:checked`)]
      .map((input) => input.value)
      .sort();
    const correct = sameAnswers(selected, question.answer);
    recordAnswer(question, selected, correct);
    return { question, selected, correct };
  });
  state.exam.submitted = true;
  saveProgress();
  const correctCount = rows.filter((row) => row.correct).length;
  els.examPaper.hidden = true;
  els.examResult.hidden = false;
  els.examSetup.hidden = false;
  els.examResult.innerHTML = `
    <h2>成绩：${correctCount} / ${rows.length}</h2>
    <p>正确率 ${Math.round((correctCount / rows.length) * 100)}%</p>
    <div class="list">
      ${rows.filter((row) => !row.correct).map((row) => `
        <article class="list-item">
          <h3>${escapeHtml(row.question.question)}</h3>
          ${figureHtml(row.question)}
          <p>你的答案：${row.selected.join("") || "未作答"} · 正确答案：${row.question.answer.join("")}</p>
        </article>
      `).join("") || `<p class="empty">本次没有错题。</p>`}
    </div>
  `;
  renderHeader();
  renderWrongList();
  renderStats();
}

function topicLabel(code) {
  return topicTitles[code] ? `${code} ${topicTitles[code]}` : code;
}

function renderStats() {
  if (state.mode !== "stats") return;
  const tree = new Map();
  for (const question of state.questions) {
    const answer = state.progress.answers[question.type];
    let children = tree;
    let code = "";
    for (const part of question.category.split(".")) {
      code = code ? code + "." + part : part;
      if (!children.has(code)) {
        children.set(code, { code, total: 0, answered: 0, correct: 0, children: new Map() });
      }
      const node = children.get(code);
      node.total++;
      if (answer) {
        node.answered++;
        if (answer.correct) node.correct++;
      }
      children = node.children;
    }
  }

  const container = els.categoryStats;
  const expanded = new Set();
  if (container.dataset.bank === state.bank) {
    container.querySelectorAll("details[open]").forEach(el => expanded.add(el.dataset.code));
  }
  container.dataset.bank = state.bank;

  function drawBranch(branch) {
    const list = document.createElement("ul");
    for (const node of branch.values()) {
      const item = document.createElement("li");
      const group = node.children.size > 0;
      const row = document.createElement(group ? "summary" : "button");
      const accuracy = node.answered ? Math.round(100 * node.correct / node.answered) + "%" : "—";
      row.className = "topic-row";
      row.innerHTML = `<strong>${escapeHtml(topicLabel(node.code))}</strong>
        <span class="topic-count">${node.answered}/${node.total} 已答 · 正确率 ${accuracy}</span>
        <progress max="${node.total}" value="${node.answered}" aria-label="答题进度"></progress>`;
      if (group) {
        const details = document.createElement("details");
        details.dataset.code = node.code;
        details.open = expanded.has(node.code);
        details.append(row, drawBranch(node.children));
        item.append(details);
      } else {
        row.type = "button";
        row.setAttribute("aria-label", `练习 ${topicLabel(node.code)}，${node.answered}/${node.total} 已答，正确率 ${accuracy}`);
        row.addEventListener("click", () => {
          // The progress tree counts the full bank, including topics outside upgrade practice.
          if (els.scopeSelect.value !== "all") {
            els.scopeSelect.value = "all";
            updateScope();
          }
          jumpToCategory(node.code);
          els.questionCategory.focus();
          els.questionText.scrollIntoView({ block: "nearest" });
        });
        item.append(row);
      }
      list.append(item);
    }
    return list;
  }
  container.replaceChildren(drawBranch(tree));
}

function sameAnswers(left, right) {
  return left.length === right.length && left.every((value, index) => value === [...right].sort()[index]);
}

function shuffle(items) {
  for (let index = items.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [items[index], items[swapIndex]] = [items[swapIndex], items[index]];
  }
  return items;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

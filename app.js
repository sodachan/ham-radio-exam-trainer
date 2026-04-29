const STORAGE_KEY = "hamExamProgressV1";

const state = {
  payload: null,
  questions: [],
  filtered: [],
  currentIndex: 0,
  mode: "practice",
  submitted: false,
  exam: null,
  currentUser: null,
  llmCache: {},
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
  state.currentUser = await fetchCurrentUser();

  try {
    state.payload = window.QUESTION_BANK || await fetchQuestionBank();
    state.questions = state.payload.questions;
    setupCategories();
    if (state.currentUser) {
      state.progress = await fetchProgress();
    }
    applyFilter();
    renderAuth();
    renderAll();
  } catch (error) {
    document.getElementById("subtitle").textContent = "题库载入失败，请用本地静态服务器打开 index.html。";
    console.error(error);
  }
}

async function fetchQuestionBank() {
  const response = await fetch("questions.json");
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

async function fetchProgress() {
  const response = await fetch("/api/progress");
  if (!response.ok) return { answers: {}, wrong: [] };
  return response.json();
}

async function authRequest(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await parseJsonResponse(response);
  if (!response.ok) {
    throw new Error(data?.detail || data?.error?.message || `${response.status} ${response.statusText}`);
  }
  return data;
}

async function login() {
  try {
    setAuthStatus("正在登录...", "loading");
    state.currentUser = await authRequest("/api/auth/login", authPayload(false));
    state.progress = await fetchProgress();
    applyFilter();
    renderAuth();
    renderAll();
    clearAuthStatus();
  } catch (error) {
    setAuthStatus(error.message, "error");
  }
}

async function register() {
  try {
    setAuthStatus("正在注册...", "loading");
    state.currentUser = await authRequest("/api/auth/register", authPayload(true));
    state.progress = await fetchProgress();
    applyFilter();
    renderAuth();
    renderAll();
    clearAuthStatus();
  } catch (error) {
    setAuthStatus(error.message, "error");
  }
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  state.currentUser = null;
  state.progress = { answers: {}, wrong: [] };
  state.llmCache = {};
  renderAuth();
}

function authPayload(includeInvite) {
  const payload = {
    username: els.authUsername.value.trim(),
    password: els.authPassword.value,
  };
  if (includeInvite) payload.invite_code = els.authInvite.value.trim();
  return payload;
}

function setAuthStatus(message, type = "") {
  els.authStatus.hidden = false;
  els.authStatus.className = `ai-status ${type}`;
  els.authStatus.textContent = message;
}

function clearAuthStatus() {
  els.authStatus.hidden = true;
  els.authStatus.className = "ai-status";
  els.authStatus.textContent = "";
}

function bindElements() {
  [
    "subtitle", "statAnswered", "statAccuracy", "statWrong", "categorySelect", "orderSelect",
    "resetProgressBtn", "questionCounter", "questionType", "questionCategory", "questionText", "questionTextContent",
    "options", "feedback", "prevBtn", "submitBtn", "nextBtn", "wrongList", "clearWrongBtn",
    "examCount", "startExamBtn", "examSetup", "examPaper", "examResult", "categoryStats",
    "jumpDialog", "jumpTitle", "jumpHint", "jumpGrid", "closeJumpBtn",
    "authView", "appShell", "authUsername", "authPassword", "authInvite", "loginBtn", "registerBtn",
    "authStatus", "currentUserLabel", "logoutBtn", "generateAiBtn", "regenerateAiBtn",
    "aiCacheInfo", "aiStatus", "aiContent", "cacheToolStatus",
  ].forEach((id) => {
    els[id] = document.getElementById(id);
  });
}

function bindEvents() {
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
  els.loginBtn.addEventListener("click", login);
  els.registerBtn.addEventListener("click", register);
  els.logoutBtn.addEventListener("click", logout);
  els.generateAiBtn.addEventListener("click", () => generateAiExplanation(false));
  els.regenerateAiBtn.addEventListener("click", () => generateAiExplanation(true));
}

function setupCategories() {
  els.categorySelect.innerHTML = `<option value="all">全部知识点</option>`;
  state.payload.categories.forEach((category) => {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    els.categorySelect.append(option);
  });
}

function loadProgress() {
  const saved = loadJson(STORAGE_KEY, null);
  if (saved && saved.answers && Array.isArray(saved.wrong)) return saved;
  return { answers: {}, wrong: [] };
}

function saveProgress() {
  if (!state.currentUser) return;
  fetch("/api/progress", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.progress),
  }).catch(() => {
    setCacheToolStatus("进度保存失败，请检查登录状态。", "error");
  });
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
    ? [...state.questions]
    : state.questions.filter((question) => question.category === category);
  if (els.orderSelect.value === "random") {
    shuffle(state.filtered);
  }
  state.currentIndex = 0;
  state.submitted = false;
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.getElementById(`${mode}View`).classList.add("active");
  renderAll();
}

function renderAll() {
  if (!state.currentUser) return;
  renderHeader();
  renderPractice();
  renderWrongList();
  renderStats();
}

function renderAuth() {
  const loggedIn = Boolean(state.currentUser);
  els.authView.hidden = loggedIn;
  els.appShell.hidden = !loggedIn;
  els.currentUserLabel.textContent = loggedIn ? `当前用户：${state.currentUser.username}` : "";
}

function renderHeader() {
  const answered = Object.keys(state.progress.answers).length;
  const correct = Object.values(state.progress.answers).filter((item) => item.correct).length;
  els.subtitle.textContent = `${state.questions.length} 道题 · ${state.payload.categories.length} 个知识点 · 进度保存在本机浏览器`;
  els.statAnswered.textContent = answered;
  els.statAccuracy.textContent = answered ? `${Math.round((correct / answered) * 100)}%` : "0%";
  els.statWrong.textContent = state.progress.wrong.length;
}

function renderPractice() {
  const question = state.filtered[state.currentIndex];
  if (!question) {
    els.questionCounter.textContent = "第 0 / 0 题";
    els.questionType.textContent = "";
    els.questionTextContent.textContent = "没有符合筛选条件的题目";
    els.options.innerHTML = "";
    renderAiPanel(null);
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
      <span><strong>${key}.</strong> ${escapeHtml(text)}</span>
    `;
    els.options.append(label);
  });

  els.prevBtn.disabled = state.currentIndex === 0;
  els.nextBtn.disabled = state.currentIndex >= state.filtered.length - 1;
  renderAiPanel(question);
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
  renderHeader();
  renderWrongList();
  renderStats();

  if (correct && state.currentIndex < state.filtered.length - 1) {
    state.currentIndex += 1;
    renderPractice();
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
  saveProgress();
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

function renderAiPanel(question) {
  clearAiStatus();
  if (!question) {
    els.aiCacheInfo.textContent = "未生成";
    els.aiContent.textContent = "没有可解析的题目。";
    els.aiContent.className = "ai-content empty";
    els.regenerateAiBtn.hidden = true;
    return;
  }
  const cached = state.llmCache[question.type];
  if (!cached) {
    els.aiCacheInfo.textContent = "未生成";
    els.aiContent.textContent = "配置 LLM 后，可以为当前题生成考点、选项辨析和助记口诀。";
    els.aiContent.className = "ai-content empty";
    els.generateAiBtn.textContent = "生成解析";
    els.regenerateAiBtn.hidden = true;
    return;
  }

  const generatedAt = cached.generatedAt ? new Date(cached.generatedAt).toLocaleString() : "未知时间";
  els.aiCacheInfo.textContent = `${cached.model || "未知模型"} · ${generatedAt}`;
  els.aiContent.className = "ai-content";
  els.aiContent.innerHTML = formatAiText(cached.content);
  els.generateAiBtn.textContent = "查看缓存";
  els.regenerateAiBtn.hidden = false;
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

  const cached = state.llmCache[question.type];
  if (cached && !force) {
    renderAiPanel(question);
    return;
  }

  try {
    els.generateAiBtn.disabled = true;
    els.regenerateAiBtn.disabled = true;
    setAiStatus("正在生成解析...", "loading");
    const result = await callLlm(question, force);
    state.llmCache[question.type] = {
      questionType: question.type,
      sourceId: question.sourceId,
      category: question.category,
      model: result.model,
      generatedAt: result.generatedAt,
      content: result.content,
    };
    renderAiPanel(question);
    setAiStatus(result.cached ? "已读取服务器缓存解析。" : "解析已生成并保存到服务器缓存。", "success");
  } catch (error) {
    setAiStatus(llmErrorMessage(error), "error");
  } finally {
    els.generateAiBtn.disabled = false;
    els.regenerateAiBtn.disabled = false;
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
  return escapeHtml(text)
    .split(/\n{2,}/)
    .map((paragraph) => `<p>${paragraph.replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function setCacheToolStatus(message, type = "") {
  els.cacheToolStatus.className = type;
  els.cacheToolStatus.textContent = message;
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
  allButton.innerHTML = `<strong>全部知识点</strong><span>${state.questions.length} 题</span>`;
  allButton.addEventListener("click", () => jumpToCategory("all"));
  els.jumpGrid.append(allButton);

  state.payload.categories.forEach((category) => {
    const questions = state.questions.filter((question) => question.category === category);
    const answered = questions.filter((question) => state.progress.answers[question.type]).length;
    const wrong = questions.filter((question) => state.progress.answers[question.type] && !state.progress.answers[question.type].correct).length;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `jump-card category-card-button ${wrong ? "status-wrong" : answered ? "status-correct" : "status-pending"}`;
    button.innerHTML = `<strong>${category}</strong><span>${answered}/${questions.length} 已答</span>`;
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
  renderPractice();
  renderStats();
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
      <p>知识点 ${question.category} · 正确答案 ${question.answer.join("")}</p>
      <button class="secondary" type="button">重刷这题</button>
    `;
    item.querySelector("button").addEventListener("click", () => jumpToQuestion(question));
    els.wrongList.append(item);
  });
}

function jumpToQuestion(question) {
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
      <div class="exam-options">
        ${Object.entries(question.options).map(([key, text]) => `
          <label class="option">
            <input name="exam-${index}" type="${question.multi ? "checkbox" : "radio"}" value="${key}">
            <span><strong>${key}.</strong> ${escapeHtml(text)}</span>
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
          <p>你的答案：${row.selected.join("") || "未作答"} · 正确答案：${row.question.answer.join("")}</p>
        </article>
      `).join("") || `<p class="empty">本次没有错题。</p>`}
    </div>
  `;
  renderHeader();
  renderWrongList();
  renderStats();
}

function renderStats() {
  const byCategory = new Map();
  state.questions.forEach((question) => {
    if (!byCategory.has(question.category)) {
      byCategory.set(question.category, { total: 0, answered: 0, correct: 0 });
    }
    const row = byCategory.get(question.category);
    row.total += 1;
    const answer = state.progress.answers[question.type];
    if (answer) {
      row.answered += 1;
      if (answer.correct) row.correct += 1;
    }
  });

  els.categoryStats.innerHTML = "";
  [...byCategory.entries()].forEach(([category, row]) => {
    const percent = row.total ? Math.round((row.answered / row.total) * 100) : 0;
    const accuracy = row.answered ? Math.round((row.correct / row.answered) * 100) : 0;
    const card = document.createElement("article");
    card.className = "category-card";
    card.innerHTML = `
      <strong>${category}</strong>
      <span>${row.answered}/${row.total} 已答 · 正确率 ${accuracy}%</span>
      <div class="meter" aria-label="${category} 进度"><div style="width:${percent}%"></div></div>
    `;
    els.categoryStats.append(card);
  });
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

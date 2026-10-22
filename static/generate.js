"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const MAX_FILE_SIZE = 20 * 1024 * 1024;
  const ACTIVE_STATUSES = new Set(["queued", "analyzing", "writing", "revising"]);
  const ASSET_TYPES = new Set(["txt", "md", "docx", "pdf", "csv", "xlsx", "png", "jpg", "jpeg", "webp"]);
  const REFERENCE_TYPES = new Set(["txt", "md", "docx", "pdf"]);
  const DRAFT_KEY = "cryptoproof.generationDraft.v1";
  const TASK_KEY = "cryptoproof.lastGeneration.v1";
  const state = {
    references: [], selected: new Set(), assets: [], currentTask: null,
    history: [], referenceLoading: true, assetUploading: false,
    creating: false, deleting: null, pollTimer: null, pollFailures: 0,
    draftTimer: null, taskViewToken: 0,
  };

  function icon(name, extraClass = "") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", `icon ${extraClass}`.trim());
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#g-${name}`);
    svg.append(use);
    return svg;
  }

  function el(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  }

  function readStorage(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : JSON.parse(value);
    } catch (_) { return fallback; }
  }

  function writeStorage(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* The page works without local storage. */ }
  }

  function notify(text, error = false) {
    $("feedback").textContent = text;
    $("feedback").classList.toggle("error", error);
    $("feedback").hidden = !text;
  }

  function showError(id, text) {
    $(id).textContent = text;
    $(id).hidden = !text;
  }

  function errorDescription(detail, fallback) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg || String(item)).join("；");
    return fallback;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    let data;
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) {
      throw new Error(errorDescription(data.detail || data.error || data.message, `请求未完成（${response.status}），请稍后重试。`));
    }
    return data;
  }

  const number = (value) => Number(value || 0).toLocaleString("zh-CN");
  const suffix = (filename) => String(filename || "").split(".").pop().toLowerCase();
  const countText = (text) => Array.from(text || "").filter((char) => !/\s/.test(char)).length;

  function formatDate(value, short = false) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return "";
    return date.toLocaleString("zh-CN", {
      ...(short ? {} : { year: "numeric" }), month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
  }

  function warningsFor(value) {
    return Array.isArray(value?.warnings) ? value.warnings.filter((item) => typeof item === "string" && item.trim()) : [];
  }

  function saveDraft() {
    writeStorage(DRAFT_KEY, {
      title: $("document-title").value, experiment_text: $("experiment-text").value,
      instructions: $("writing-instructions").value, target_words: $("target-words").value,
      reference_ids: [...state.selected], assets: state.assets,
    });
  }

  function scheduleSave() {
    clearTimeout(state.draftTimer);
    state.draftTimer = setTimeout(saveDraft, 350);
  }

  function restoreDraft() {
    const draft = readStorage(DRAFT_KEY, null);
    if (!draft || typeof draft !== "object") return;
    for (const [id, field] of [["document-title", "title"], ["experiment-text", "experiment_text"], ["writing-instructions", "instructions"]]) {
      if (typeof draft[field] === "string") $(id).value = draft[field];
    }
    if (Number(draft.target_words) >= 300 && Number(draft.target_words) <= 6000) $("target-words").value = draft.target_words;
    state.selected = new Set(Array.isArray(draft.reference_ids) ? draft.reference_ids.slice(0, 6) : []);
    state.assets = Array.isArray(draft.assets) ? draft.assets.filter((asset) => asset && typeof asset.id === "string").slice(0, 8) : [];
  }

  function isActive(task = state.currentTask) { return Boolean(task && ACTIVE_STATUSES.has(task.status)); }

  function updateControls() {
    const hasMaterial = Boolean($("experiment-text").value.trim() || state.assets.length);
    const words = Number($("target-words").value);
    const validWords = Number.isInteger(words) && words >= 300 && words <= 6000;
    const busy = state.creating || isActive();
    $("generate").disabled = busy || state.assetUploading || state.referenceLoading || !hasMaterial || !state.selected.size || !validWords;
    $("generate-label").textContent = state.creating ? "正在提交…" : isActive() ? "文档生成中…" : "生成文档";
    document.body.classList.toggle("busy", busy);
    $("generate-hint").textContent = busy ? "可在下方查看进度，完成后导出文档" : state.assetUploading ? "正在上传实验附件，请稍候" : !state.selected.size ? "先选择至少一份参考资料" : !hasMaterial ? "添加实验文字或附件即可开始" : !validWords ? "目标字数请输入 300–6,000 之间的整数" : `将参考 ${state.selected.size} 份资料，围绕本次实验展开`;
    $("experiment-count").textContent = number(countText($("experiment-text").value));
    $("selection-count").textContent = `${state.selected.size} / 6`;
    $("asset-file").disabled = state.assetUploading || state.assets.length >= 8;
    $("asset-dropzone").classList.toggle("uploading", state.assetUploading);
    $("asset-upload-label").textContent = state.assetUploading ? "正在读取并保存附件…" : state.assets.length >= 8 ? "已添加 8 个附件，可移除后继续添加" : "添加实验附件，或拖放至此";
    document.querySelectorAll("[data-words]").forEach((button) => {
      button.classList.toggle("active", Number(button.dataset.words) === words);
      button.setAttribute("aria-pressed", String(Number(button.dataset.words) === words));
    });
  }

  function renderAssets() {
    const container = $("asset-list");
    container.replaceChildren();
    for (const asset of state.assets) {
      const row = el("div", "asset");
      row.append(icon(["png", "jpg", "jpeg", "webp"].includes(suffix(asset.filename)) ? "image" : "doc"));
      const copy = el("div", "asset-copy");
      copy.append(el("strong", "", asset.filename || "实验附件"));
      const details = [];
      if (asset.char_count) details.push(`${number(asset.char_count)} 字`);
      if (asset.table_count) details.push(`${number(asset.table_count)} 个表格`);
      if (asset.image_count) details.push(`${number(asset.image_count)} 张图片`);
      if (!details.length) details.push(asset.kind === "image" ? "图片素材，生成时分析" : "实验素材已保存");
      copy.append(el("small", "", details.join(" · ")));
      const warnings = warningsFor(asset);
      if (warnings.length) copy.append(el("small", "asset-warning", warnings.join("；")));
      const remove = el("button", "icon-button danger");
      remove.type = "button";
      remove.setAttribute("aria-label", `移除附件 ${asset.filename || ""}`);
      remove.title = "从本次写作移除";
      remove.append(icon("close"));
      remove.addEventListener("click", () => {
        state.assets = state.assets.filter((item) => item.id !== asset.id);
        renderAssets(); updateControls(); saveDraft();
      });
      row.append(copy, remove);
      container.append(row);
    }
  }

  function renderReferences() {
    const container = $("reference-list");
    container.replaceChildren();
    if (!state.references.length) {
      const empty = el("div", "library-empty");
      empty.append(icon("book"), el("div", "", "还没有参考资料"), el("span", "", "添加论文、分析文档或报告，建立你的写作资料库"));
      container.append(empty);
    }
    for (const reference of state.references) {
      const row = el("div", `reference-item${state.selected.has(reference.id) ? " selected" : ""}`);
      const checkbox = el("input");
      checkbox.type = "checkbox";
      checkbox.id = `reference-${reference.id}`;
      checkbox.checked = state.selected.has(reference.id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked && state.selected.size >= 6) {
          checkbox.checked = false;
          notify("一次最多选择 6 份参考资料，请先取消一份。", true);
          return;
        }
        if (checkbox.checked) state.selected.add(reference.id); else state.selected.delete(reference.id);
        row.classList.toggle("selected", checkbox.checked);
        updateControls(); saveDraft();
      });
      const label = el("label", "reference-copy");
      label.htmlFor = checkbox.id;
      label.append(el("strong", "", reference.title || reference.filename || "参考资料"));
      const details = [suffix(reference.filename).toUpperCase(), `${number(reference.char_count)} 字`].filter(Boolean);
      if (reference.table_count) details.push(`${number(reference.table_count)} 个表格`);
      if (reference.image_count) details.push(`${number(reference.image_count)} 张图片`);
      if (warningsFor(reference).length) details.push("有读取提示");
      label.append(el("small", "", details.join(" · ")));
      const actions = el("div", "reference-actions");
      const preview = el("button", "icon-button");
      preview.type = "button"; preview.title = "预览资料";
      preview.setAttribute("aria-label", `预览 ${reference.title || reference.filename}`);
      preview.append(icon("eye"));
      preview.addEventListener("click", () => previewReference(reference));
      const remove = el("button", "icon-button danger");
      remove.type = "button"; remove.title = "删除资料";
      remove.setAttribute("aria-label", `删除 ${reference.title || reference.filename}`);
      remove.append(icon("trash"));
      remove.addEventListener("click", () => {
        state.deleting = reference;
        $("delete-reference-description").textContent = `将从参考资料库中删除「${reference.title || reference.filename}」。`;
        showError("delete-reference-error", "");
        $("delete-reference-dialog").showModal();
      });
      actions.append(preview, remove);
      row.append(checkbox, label, actions);
      container.append(row);
    }
    updateControls();
  }

  async function loadReferences() {
    try {
      const data = await api("/api/references");
      state.references = Array.isArray(data.references) ? data.references : [];
      const valid = new Set(state.references.map((reference) => reference.id));
      state.selected = new Set([...state.selected].filter((id) => valid.has(id)).slice(0, 6));
      state.referenceLoading = false;
      renderReferences();
    } catch (error) {
      state.referenceLoading = false;
      $("reference-list").replaceChildren(el("div", "library-empty", "参考资料读取失败，请刷新页面重试。"));
      notify(error.message, true);
      updateControls();
    }
  }

  async function previewReference(reference) {
    const dialog = $("preview-reference-dialog");
    $("preview-reference-title").textContent = reference.title || reference.filename || "资料预览";
    $("preview-reference-meta").textContent = `${reference.filename || ""} · ${number(reference.char_count)} 字`;
    $("preview-reference-body").textContent = "正在读取资料…";
    dialog.showModal();
    try {
      const data = await api(`/api/references/${encodeURIComponent(reference.id)}`);
      if (!dialog.open || $("preview-reference-title").textContent !== (reference.title || reference.filename || "资料预览")) return;
      const warnings = warningsFor(data);
      $("preview-reference-meta").textContent = [reference.filename, `${number(data.char_count)} 字`, ...warnings].filter(Boolean).join(" · ");
      $("preview-reference-body").textContent = data.text || data.preview || "未提取到可预览的正文。";
    } catch (error) { $("preview-reference-body").textContent = error.message; }
  }

  function validateFile(file, allowed) {
    if (file.size > MAX_FILE_SIZE) throw new Error(`「${file.name}」超过 20 MB，请缩小文件后重试。`);
    if (!allowed.has(suffix(file.name))) throw new Error(`暂不支持「${file.name}」的格式，请选择页面列出的文件类型。`);
    if (!file.size) throw new Error(`「${file.name}」是空文件，请检查内容。`);
  }

  async function uploadAssets(files) {
    if (state.assetUploading || !files.length) return;
    if (state.assets.length + files.length > 8) {
      notify("每次写作最多添加 8 个附件，请减少文件数量。", true);
      $("asset-file").value = "";
      return;
    }
    try { files.forEach((file) => validateFile(file, ASSET_TYPES)); }
    catch (error) { notify(error.message, true); $("asset-file").value = ""; return; }
    state.assetUploading = true; updateControls();
    let uploaded = 0;
    const errors = [];
    for (const file of files) {
      try {
        const form = new FormData(); form.append("file", file);
        const data = await api("/api/generation/assets", { method: "POST", body: form });
        state.assets.push(data); uploaded++;
        renderAssets(); saveDraft();
      } catch (error) { errors.push(`${file.name}：${error.message}`); }
    }
    state.assetUploading = false;
    $("asset-file").value = "";
    updateControls();
    notify(errors.length ? `已添加 ${uploaded} 个附件。${errors.join("；")}` : `已添加 ${uploaded} 个实验附件。`, Boolean(errors.length));
  }

  function statusLabel(status) {
    return ({ queued: "等待生成", analyzing: "分析素材", writing: "正在写作", revising: "复核篇幅", completed: "已完成", failed: "未完成" })[status] || "待查看";
  }

  function renderHistory() {
    const select = $("history-select");
    select.replaceChildren();
    const empty = el("option", "", state.history.length ? "选择一篇文档，恢复结果或查看进度" : "暂无生成记录");
    empty.value = ""; select.append(empty);
    for (const task of state.history) {
      const option = el("option", "", `${formatDate(task.created_at, true)} · ${task.title || "未命名文档"} · ${statusLabel(task.status)}`);
      option.value = task.id;
      select.append(option);
    }
    if (state.currentTask && state.history.some((task) => task.id === state.currentTask.id)) select.value = state.currentTask.id;
  }

  async function loadHistory(silent = false) {
    try {
      const data = await api("/api/generations");
      state.history = Array.isArray(data.generations) ? data.generations : [];
      renderHistory();
    } catch (error) { if (!silent) notify(error.message, true); }
  }

  function renderSources(task) {
    const sources = $("source-list"); sources.replaceChildren();
    for (const [heading, items, field] of [["参考资料", task.reference_sources, "title"], ["实验附件", task.asset_sources, "filename"]]) {
      if (!Array.isArray(items) || !items.length) continue;
      const group = el("div", "source-group"); group.append(el("strong", "", heading));
      for (const item of items) group.append(el("span", "source-chip", item[field] || item.filename || "未命名资料"));
      sources.append(group);
    }
    $("source-details").hidden = !sources.childElementCount;
    $("fact-details").hidden = !task.fact_summary;
    $("fact-summary").textContent = task.fact_summary || "";
  }

  function renderTask(task) {
    const active = isActive(task);
    const completed = task.status === "completed";
    const failed = task.status === "failed";
    const hasBody = Boolean(task.body && String(task.body).trim());
    $("result-empty").hidden = true;
    $("generation-progress").hidden = !active;
    $("result-document").hidden = !hasBody;
    $("result-error").hidden = !failed;
    $("copy-result").disabled = !completed || !hasBody;
    $("download-result").disabled = !completed || !hasBody;
    $("output-caption").textContent = active ? "正在根据本次素材与所选资料组织内容。" : failed ? "生成中断，素材草稿已保留，可调整后重试。" : completed && hasBody ? "文档已生成，请核对实验事实、数据与专业结论。" : "暂未生成可用正文，请查看提示后重试。";
    if (active) {
      $("progress-title").textContent = statusLabel(task.status);
      $("progress-message").textContent = task.message || "正在整理实验素材和参考资料，请稍候。";
      const stages = ["analyzing", "writing", "revising"];
      const current = stages.indexOf(task.status);
      document.querySelectorAll(".progress-steps li").forEach((item, index) => {
        item.classList.toggle("current", current === index);
        item.classList.toggle("done", current > index);
      });
    }
    if (hasBody) {
      $("result-title").textContent = task.title || "实验分析文档";
      $("result-body").textContent = task.body;
      $("result-date").textContent = formatDate(task.created_at);
      const actual = Number.isFinite(Number(task.word_count)) ? Number(task.word_count) : countText(task.body);
      const within = task.within_target === true;
      $("result-word-count").textContent = `正文 ${number(actual)} 字 · 目标 ${number(task.target_words)} 字${task.within_target === false ? " · 超出目标范围" : within ? " · 在目标范围内" : ""}`;
      $("result-word-count").classList.toggle("word-warning", task.within_target === false);
    }
    const warnings = [...warningsFor(task)];
    if (completed && task.within_target === false && !warnings.some((warning) => /字数|篇幅/.test(warning))) warnings.push("正文篇幅超出目标字数的 ±15%，请根据实际需要调整。");
    if (completed && !hasBody) warnings.push("未返回可用正文，请重新生成或检查模型配置。");
    $("generation-warning-list").replaceChildren(...warnings.map((warning) => el("li", "", warning)));
    $("generation-notes").hidden = !warnings.length;
    if (failed) $("result-error-text").textContent = task.error || task.message || "生成未完成，请检查素材与模型服务后重试。";
    renderSources(task);
    updateControls(); renderHistory(); updateElapsed();
  }

  function updateElapsed() {
    if (!isActive() || !state.currentTask.created_at) return;
    const start = new Date(state.currentTask.created_at).valueOf();
    if (!Number.isFinite(start)) return;
    const elapsed = Math.max(0, Math.floor((Date.now() - start) / 1000));
    $("progress-elapsed").textContent = elapsed < 60 ? `${elapsed} 秒` : `${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`;
  }

  function cancelPoll() { clearTimeout(state.pollTimer); state.pollTimer = null; }

  function setTask(task) {
    state.currentTask = task;
    writeStorage(TASK_KEY, task.id);
    renderTask(task);
  }

  async function pollTask(id, token) {
    if (token !== state.taskViewToken) return;
    try {
      const task = await api(`/api/generations/${encodeURIComponent(id)}`);
      if (token !== state.taskViewToken) return;
      if (state.pollFailures >= 3) notify("连接已恢复，继续显示生成进度。");
      state.pollFailures = 0;
      setTask(task);
      if (isActive(task)) state.pollTimer = setTimeout(() => pollTask(id, token), 1800);
      else await loadHistory(true);
    } catch (error) {
      if (token !== state.taskViewToken) return;
      state.pollFailures++;
      $("progress-message").textContent = "连接暂时中断，正在重新获取生成进度；已提交的任务会在后台继续。";
      if (state.pollFailures === 3) notify(`暂时无法读取进度：${error.message} 正在自动重试，也可稍后从最近生成恢复。`, true);
      state.pollTimer = setTimeout(() => pollTask(id, token), Math.min(10000, 2000 * state.pollFailures));
    }
  }

  async function openTask(id, shouldScroll = false) {
    if (!id) return;
    cancelPoll();
    const token = ++state.taskViewToken;
    try {
      const task = await api(`/api/generations/${encodeURIComponent(id)}`);
      if (token !== state.taskViewToken) return;
      state.pollFailures = 0; setTask(task);
      if (isActive(task)) state.pollTimer = setTimeout(() => pollTask(id, token), 1800);
      if (shouldScroll) $("output-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { if (token === state.taskViewToken) notify(`无法恢复这次生成：${error.message}`, true); }
  }

  async function createGeneration() {
    if (state.creating || state.assetUploading || isActive()) return;
    const target = Number($("target-words").value);
    if (!state.selected.size) { notify("请先选择至少一份参考资料。", true); return; }
    if (!$("experiment-text").value.trim() && !state.assets.length) { notify("请提供实验结果文字或上传实验附件。", true); return; }
    if (!Number.isInteger(target) || target < 300 || target > 6000) { notify("目标字数请输入 300–6,000 之间的整数。", true); $("target-words").focus(); return; }
    saveDraft();
    state.creating = true; updateControls(); notify("");
    try {
      const task = await api("/api/generations", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: $("document-title").value.trim(), experiment_text: $("experiment-text").value.trim(), instructions: $("writing-instructions").value.trim(), target_words: target, reference_ids: [...state.selected], asset_ids: state.assets.map((asset) => asset.id) }),
      });
      cancelPoll();
      const token = ++state.taskViewToken;
      state.pollFailures = 0; setTask(task);
      if (isActive(task)) state.pollTimer = setTimeout(() => pollTask(task.id, token), 1000);
      await loadHistory(true);
      $("output-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { notify(error.message, true); }
    finally { state.creating = false; updateControls(); }
  }

  async function copyResult() {
    const body = state.currentTask?.body;
    if (!body) return;
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(body);
      else {
        const textarea = el("textarea"); textarea.value = body;
        textarea.style.position = "fixed"; textarea.style.left = "-9999px";
        document.body.append(textarea); textarea.select();
        const copied = document.execCommand("copy"); textarea.remove();
        if (!copied) throw new Error("请选中正文后手动复制。");
      }
      $("copy-result").replaceChildren(icon("check"), document.createTextNode("已复制"));
      setTimeout(() => $("copy-result").replaceChildren(icon("copy"), document.createTextNode("复制正文")), 1800);
    } catch (error) { notify(`复制未完成：${error.message || "请选中正文后手动复制。"}`, true); }
  }

  async function downloadResult() {
    const task = state.currentTask;
    if (!task || task.status !== "completed" || !task.body) return;
    $("download-result").disabled = true;
    try {
      const response = await fetch(`/api/generations/${encodeURIComponent(task.id)}/download.docx`);
      if (!response.ok) {
        let data = {}; try { data = await response.json(); } catch (_) { /* fall through */ }
        throw new Error(errorDescription(data.detail, "下载未完成，请重试。"));
      }
      const blob = await response.blob();
      const link = el("a"); const url = URL.createObjectURL(blob);
      link.href = url; link.download = `${(task.title || "实验分析文档").replace(/[\\/:*?"<>|]/g, "_")}.docx`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) { notify(error.message, true); }
    finally { $("download-result").disabled = state.currentTask?.status !== "completed" || !state.currentTask?.body; }
  }

  $("open-add").addEventListener("click", () => {
    $("reference-form").reset();
    $("reference-file-label").textContent = "选择参考文档";
    $("save-reference").replaceChildren(document.createTextNode(state.selected.size < 6 ? "添加并选用" : "添加到资料库"), icon("arrow"));
    showError("reference-form-error", "");
    $("add-reference-dialog").showModal();
  });

  $("reference-file").addEventListener("change", () => {
    $("reference-file-label").textContent = $("reference-file").files[0]?.name || "选择参考文档";
  });

  $("reference-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = $("reference-file").files[0];
    if (!file || $("save-reference").disabled) return;
    showError("reference-form-error", "");
    try { validateFile(file, REFERENCE_TYPES); }
    catch (error) { showError("reference-form-error", error.message); return; }
    $("save-reference").disabled = true;
    $("save-reference").textContent = "正在读取资料…";
    try {
      const form = new FormData(); form.append("file", file);
      if ($("reference-title").value.trim()) form.append("title", $("reference-title").value.trim());
      const reference = await api("/api/references", { method: "POST", body: form });
      state.references = [reference, ...state.references.filter((item) => item.id !== reference.id)];
      const selected = state.selected.size < 6;
      if (selected) state.selected.add(reference.id);
      renderReferences(); saveDraft();
      $("add-reference-dialog").close();
      const warnings = warningsFor(reference);
      notify(`「${reference.title || reference.filename}」已保存${selected ? "并选用" : "，可以取消其他选择后使用"}。${warnings.length ? `读取提示：${warnings.join("；")}` : ""}`, Boolean(warnings.length));
    } catch (error) { showError("reference-form-error", error.message); }
    finally {
      $("save-reference").disabled = false;
      $("save-reference").replaceChildren(document.createTextNode("添加并选用"), icon("arrow"));
    }
  });

  $("confirm-delete-reference").addEventListener("click", async () => {
    const reference = state.deleting;
    if (!reference || $("confirm-delete-reference").disabled) return;
    $("confirm-delete-reference").disabled = true;
    showError("delete-reference-error", "");
    try {
      await api(`/api/references/${encodeURIComponent(reference.id)}`, { method: "DELETE" });
      state.references = state.references.filter((item) => item.id !== reference.id);
      state.selected.delete(reference.id); renderReferences(); saveDraft();
      $("delete-reference-dialog").close();
      notify(`已删除「${reference.title || reference.filename}」。`);
    } catch (error) { showError("delete-reference-error", error.message); }
    finally { $("confirm-delete-reference").disabled = false; }
  });

  document.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => $(button.dataset.close).close());
  });

  $("asset-file").addEventListener("change", () => uploadAssets(Array.from($("asset-file").files)));
  const zone = $("asset-dropzone");
  for (const name of ["dragenter", "dragover"]) zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add("drag-over"); });
  for (const name of ["dragleave", "drop"]) zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove("drag-over"); });
  zone.addEventListener("drop", (event) => uploadAssets(Array.from(event.dataTransfer?.files || [])));
  for (const id of ["document-title", "experiment-text", "writing-instructions", "target-words"]) $(id).addEventListener("input", () => { updateControls(); scheduleSave(); });
  document.querySelectorAll("[data-words]").forEach((button) => button.addEventListener("click", () => { $("target-words").value = button.dataset.words; updateControls(); saveDraft(); }));
  $("generate").addEventListener("click", createGeneration);
  $("retry-generation").addEventListener("click", createGeneration);
  $("copy-result").addEventListener("click", copyResult);
  $("download-result").addEventListener("click", downloadResult);
  $("history-select").addEventListener("change", () => openTask($("history-select").value));
  $("refresh-history").addEventListener("click", async () => {
    $("refresh-history").disabled = true;
    await loadHistory();
    if (state.currentTask) await openTask(state.currentTask.id);
    $("refresh-history").disabled = false;
  });
  window.addEventListener("pagehide", saveDraft);
  setInterval(updateElapsed, 1000);

  async function initialize() {
    restoreDraft(); renderAssets(); updateControls();
    await Promise.allSettled([loadReferences(), loadHistory(true)]);
    const lastTask = readStorage(TASK_KEY, null);
    if (lastTask && state.history.some((task) => task.id === lastTask)) await openTask(lastTask);
  }
  initialize();
})();

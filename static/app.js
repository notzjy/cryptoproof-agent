'use strict';

const $ = id => document.getElementById(id);
const example = '本系统采用数子签名技术校验文件完整性，并使用数字正书验证签名身份。\n为了实现非对称加蜜，系统还需要完善秘钥管理流程。';
let task = null;
let libraries = [];
let taskBusy = false;
let libraryBusy = false;
let deleteTarget = null;
const storageKey = 'cryptoproof.selected-library';

function selectedLibrary() { return libraries.find(item => item.id === $('library-select').value); }
function message(text, error = false) {
  $('message').hidden = !text;
  $('message').textContent = text;
  $('message').classList.toggle('error', error);
}
async function jsonResponse(response) {
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof result.detail === 'string' ? result.detail : `请求未成功（${response.status}），请检查输入后重试。`;
    const error = new Error(detail); error.status = response.status; throw error;
  }
  return result;
}
function storedLibrary() { try { return localStorage.getItem(storageKey); } catch { return null; } }
function rememberLibrary(id) { try { if (id) localStorage.setItem(storageKey, id); else localStorage.removeItem(storageKey); } catch {} }
function syncControls() {
  const busy = taskBusy || libraryBusy;
  const hasLibrary = !!selectedLibrary();
  $('run').disabled = busy || !hasLibrary || !$('text').value.trim();
  $('upload').disabled = busy || !hasLibrary || !$('file').files.length;
  $('file').disabled = busy;
  $('text').disabled = taskBusy;
  $('example').disabled = busy;
  $('library-select').disabled = busy || !libraries.length;
  $('add-library').disabled = busy;
  $('delete-library').disabled = busy || !hasLibrary;
  const waiting = task?.status === 'awaiting_review';
  $('approve').disabled = busy || !waiting;
  $('none').disabled = busy || !waiting;
  document.querySelectorAll('.editbox').forEach(box => { box.disabled = busy || !waiting; });
  $('download').disabled = busy || task?.status !== 'completed';
  $('copy').disabled = busy || typeof task?.corrected_text !== 'string';
  $('run-label').textContent = taskBusy ? '正在处理…' : '开始智能校对';
  document.body.classList.toggle('busy', taskBusy);
}
function countText() { $('char-count').textContent = Array.from($('text').value).length.toLocaleString(); syncControls(); }
function renderLibrary() {
  const library = selectedLibrary();
  $('input-library').textContent = library?.name || '请先添加术语库';
  $('term-count').textContent = library ? library.term_count.toLocaleString() : '0';
  $('library-domain').textContent = library?.domain || '暂无术语库';
  $('library-caption').textContent = library ? `来源：${library.filename}` : '上传一个 TXT，开始你的专业领域校对';
  $('term-preview').replaceChildren();
  for (const term of library?.preview || []) {
    const chip = document.createElement('span'); chip.className = 'term-chip'; chip.textContent = term;
    $('term-preview').append(chip);
  }
  rememberLibrary(library?.id);
  syncControls();
}
async function refreshLibraries(preferred) {
  const result = await jsonResponse(await fetch('/api/libraries'));
  libraries = result.libraries;
  const preferredId = preferred || storedLibrary();
  const chosen = libraries.find(item => item.id === preferredId)?.id || result.default_library_id;
  $('library-select').replaceChildren();
  if (!libraries.length) $('library-select').append(new Option('暂无术语库，请点击右上角添加', ''));
  for (const library of libraries) $('library-select').append(new Option(library.name, library.id));
  if (chosen) $('library-select').value = chosen;
  renderLibrary();
}
function render(result) {
  task = result;
  const waiting = result.status === 'awaiting_review';
  const statusText = waiting ? '待人工审核' : result.status === 'incomplete' ? '检查未完成' : '审核已完成';
  $('review-empty').hidden = true;
  $('review-details').hidden = false;
  $('edit-count').textContent = `${result.edits.length} 处建议`;
  $('status').textContent = `${statusText} · 本次使用：${result.library?.name || '原任务术语库'} · ${result.task_id.slice(0, 8)}`;
  $('summary').textContent = [result.summary, ...(result.warnings || [])].filter(Boolean).join('\n');
  $('summary').classList.toggle('warning', result.analysis_complete === false || !!result.warnings?.length);
  $('edits').replaceChildren();
  for (const edit of result.edits) {
    const card = document.createElement('div'); card.className = 'edit';
    const label = document.createElement('label');
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.className = 'editbox';
    checkbox.value = edit.id; checkbox.checked = edit.risk === 'low'; checkbox.disabled = !waiting;
    const content = document.createElement('span');
    const old = document.createElement('span'); old.className = 'old'; old.textContent = edit.original;
    const arrow = document.createElement('span'); arrow.className = 'edit-arrow'; arrow.textContent = '→';
    const replacement = document.createElement('span'); replacement.className = 'replacement'; replacement.textContent = edit.replacement || '（删除）';
    content.append(old, arrow, replacement); label.append(checkbox, content);
    const reason = document.createElement('small'); reason.textContent = edit.reason;
    card.append(label, reason); $('edits').append(card);
  }
  if (!result.edits.length) {
    const empty = document.createElement('p'); empty.className = 'summary';
    empty.textContent = result.analysis_complete === false ? '检查尚未完成，请按提示重试。' : '本次未生成修改建议，判断依据见上方说明。';
    $('edits').append(empty);
  }
  const hasOutput = typeof result.corrected_text === 'string';
  $('output-placeholder').hidden = hasOutput; $('output').hidden = !hasOutput;
  $('output').textContent = result.corrected_text || '';
  $('result-label').textContent = result.status === 'completed'
    ? result.analysis_complete === false ? '已生成已确认部分的修订稿' : '修订稿已生成'
    : result.status === 'incomplete' ? '请重新检查' : '等待审核完成';
  const activeStep = waiting ? 1 : result.status === 'completed' ? 2 : 0;
  document.querySelectorAll('.workflow>span').forEach((element, index) => element.classList.toggle('current', index === activeStep));
  if (result.status === 'completed') {
    const count = result.approved_ids?.length || 0;
    const note = result.analysis_complete === false
      ? '部分检查未完成，请参考说明继续复核；已确认的修改可以复制或下载。'
      : result.warnings?.length ? '部分候选未采用，详情见修改说明；可以复制或下载修订稿。'
      : '可以复制文本或下载 Word 文档。';
    const prefix = result.review_completed ? count ? `已采纳 ${count} 处修改，` : '审核已完成，' : '';
    message(`${prefix}修订稿已生成。${note}`);
  } else if (waiting) {
    message(result.analysis_complete === false
      ? '部分检查未完成，已通过校验的建议仍可勾选采纳，或重新校对。'
      : '修改建议已生成，请勾选需要采纳的项目。', result.analysis_complete === false);
  } else {
    message('检查未完成，请查看修改说明后重新校对。', true);
  }
  syncControls();
}
async function submitTask(upload = false) {
  const library = selectedLibrary();
  if (!library || taskBusy || libraryBusy) return;
  const file = $('file').files[0];
  if (upload && (!file || file.size > 3 * 1024 * 1024)) { message('请选择不超过 3MB 的文档。', true); return; }
  taskBusy = true; syncControls(); message(`正在使用「${library.name}」校对，请稍候…`);
  try {
    let response;
    if (upload) {
      const data = new FormData(); data.append('file', file); data.append('library_id', library.id);
      response = await fetch('/api/tasks/upload', { method: 'POST', body: data });
    } else {
      response = await fetch('/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: $('text').value, library_id: library.id }) });
    }
    const result = await jsonResponse(response);
    if (upload) { $('text').value = result.original_text; countText(); }
    render(result);
  } catch (error) {
    message(error.message, true);
    if (error.status === 409) await refreshLibraries().catch(() => {});
  } finally { taskBusy = false; syncControls(); }
}
async function resume(ids) {
  if (!task || taskBusy) return;
  taskBusy = true; syncControls(); message('正在应用你确认的修改…');
  try {
    render(await jsonResponse(await fetch(`/api/tasks/${task.task_id}/approve`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approved_ids: ids }) })));
  } catch (error) {
    // 重复点击或前次响应中断时，先核实任务是否已经成功采纳。
    let recovered = false;
    if (error.status === 409 || !error.status) {
      try {
        const current = await jsonResponse(await fetch(`/api/tasks/${task.task_id}`));
        if (current.status === 'completed') { render(current); recovered = true; }
      } catch {}
    }
    if (!recovered) message(error.message, true);
  }
  finally { taskBusy = false; syncControls(); }
}
$('text').addEventListener('input', countText);
$('example').onclick = () => { $('text').value = example; countText(); };
$('run').onclick = () => submitTask();
$('upload').onclick = () => submitTask(true);
$('file').onchange = () => { $('document-caption').textContent = $('file').files[0]?.name || '支持 TXT、DOCX、文字型 PDF · 文件不超过 3MB'; syncControls(); };
$('approve').onclick = () => resume([...document.querySelectorAll('.editbox:checked')].map(box => box.value));
$('none').onclick = () => resume([]);
$('library-select').onchange = () => { renderLibrary(); message(`已选择「${selectedLibrary().name}」，将用于下一次校对。`); };
$('copy').onclick = async () => {
  try { await navigator.clipboard.writeText(task.corrected_text); message('校对结果已复制。'); }
  catch { message('复制未成功，请在结果区域手动选择文本复制。', true); }
};
$('download').onclick = async () => {
  try {
    const response = await fetch(`/api/tasks/${task.task_id}/download.docx`);
    if (!response.ok) { await jsonResponse(response); return; }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = '校对结果.docx'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (error) { message(error.message, true); }
};
document.querySelectorAll('[data-close]').forEach(button => { button.onclick = () => { if (!libraryBusy) $(button.dataset.close).close(); }; });
for (const id of ['add-dialog', 'delete-dialog']) $(id).addEventListener('cancel', event => { if (libraryBusy) event.preventDefault(); });
$('add-library').onclick = () => {
  $('library-form').reset(); $('library-error').hidden = true;
  $('library-file-label').textContent = '选择术语 TXT 文件'; $('add-dialog').showModal();
};
$('library-file').onchange = () => { $('library-file-label').textContent = $('library-file').files[0]?.name || '选择术语 TXT 文件'; };
$('library-form').onsubmit = async event => {
  event.preventDefault(); if (libraryBusy) return;
  $('library-error').hidden = true;
  const file = $('library-file').files[0];
  if (!file || !file.name.toLowerCase().endsWith('.txt') || file.size > 1024 * 1024) {
    $('library-error').textContent = '请选择不超过 1MB 的 TXT 文件。'; $('library-error').hidden = false; return;
  }
  libraryBusy = true; syncControls(); $('save-library').disabled = true; $('save-library').textContent = '正在添加…';
  try {
    const created = await jsonResponse(await fetch('/api/libraries', { method: 'POST', body: new FormData($('library-form')) }));
    await refreshLibraries(created.id); $('add-dialog').close(); message(`已添加并选择「${created.name}」，共 ${created.term_count} 个术语。`);
  } catch (error) { $('library-error').textContent = error.message; $('library-error').hidden = false; }
  finally { libraryBusy = false; $('save-library').disabled = false; $('save-library').textContent = '添加并使用'; syncControls(); }
};
$('delete-library').onclick = () => {
  deleteTarget = selectedLibrary(); if (!deleteTarget) return;
  $('delete-description').textContent = `将删除「${deleteTarget.name}」及其中的 ${deleteTarget.term_count} 个术语。此操作无法直接撤销。`;
  $('delete-error').hidden = true; $('delete-dialog').showModal();
};
$('confirm-delete').onclick = async () => {
  if (!deleteTarget || libraryBusy) return;
  libraryBusy = true; syncControls(); $('confirm-delete').disabled = true;
  try {
    await jsonResponse(await fetch(`/api/libraries/${deleteTarget.id}`, { method: 'DELETE' }));
    await refreshLibraries(); $('delete-dialog').close(); message(`已删除「${deleteTarget.name}」。`);
  } catch (error) { $('delete-error').textContent = error.message; $('delete-error').hidden = false; }
  finally { libraryBusy = false; $('confirm-delete').disabled = false; syncControls(); }
};
async function initialize() {
  countText();
  const results = await Promise.allSettled([
    refreshLibraries(),
    fetch('/api/health').then(jsonResponse).then(health => {
      $('mode-text').textContent = health.mode === 'llm' ? `模型模式 · ${health.model}` : '离线演示模式';
      $('mode-tip').lastElementChild.textContent = health.mode === 'llm'
        ? '模型负责普通纠错，并按需核实所选领域的专业术语。所有修改需人工确认。'
        : '当前为离线演示。启用 LLM 模式后，才能使用模型进行跨领域纠错与术语评估。';
    }),
  ]);
  if (results[0].status === 'rejected') {
    $('library-select').replaceChildren(new Option('加载失败，请刷新重试', ''));
    message('术语库加载失败，请确认服务正常后刷新页面。', true);
  }
  if (results[1].status === 'rejected') { $('mode-text').textContent = '服务连接失败'; $('mode').classList.add('offline'); }
  syncControls();
}
initialize();

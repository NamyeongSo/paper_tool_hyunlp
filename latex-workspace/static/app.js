import { PDFPreview } from '/pdf-preview.js';
import { SentenceComments, sentenceRange } from '/sentence-comments.js';

const $ = id => document.getElementById(id);
const icon = name => `<svg class="icon"><use href="#i-${name}"/></svg>`;
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const state = { projects: [], project: null, tree: [], docs: new Map(), current: null, openFolders: new Set(), job: null, compileAgain: false, switching: false };
let saveTimer, autoTimer, toastTimer;
const channel = 'BroadcastChannel' in window ? new BroadcastChannel('folio-pdf') : null;

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  let result;
  try { result = await response.json(); } catch { throw new Error('서버 응답을 읽을 수 없습니다. 서버가 실행 중인지 확인해 주세요.'); }
  if (!response.ok) { const error = new Error(result.error || '요청에 실패했습니다.'); error.status = response.status; throw error; }
  return result;
}
function query(path, values) { return `${path}?${new URLSearchParams(values)}`; }
function remember(key, value) { try { localStorage.setItem('folio:' + key, JSON.stringify(value)); } catch {} }
function recall(key, fallback) { try { return JSON.parse(localStorage.getItem('folio:' + key)) ?? fallback; } catch { return fallback; } }
function toast(message, error = false) { $('toast').textContent = message; $('toast').classList.toggle('error', error); $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, error ? 8000 : 3500); }
function allFiles(items = state.tree) { return items.flatMap(item => item.type === 'folder' ? allFiles(item.children) : [item]); }
function fileSymbol(name) {
  const ext = name.split('.').pop().toLowerCase();
  return ext === 'tex' ? '<span class="file-symbol">Tₑ</span>' : ext === 'bib' ? '<span class="file-symbol bib">B</span>' : ext === 'pdf' ? '<span class="file-symbol pdf">PDF</span>' : icon('file');
}
function draftKey(doc) { return `draft:${doc.project}:${doc.path}`; }
function rememberDraft(doc) { remember(draftKey(doc), doc.dirty ? { content: doc.cm.getValue(), revision: doc.revision } : null); }

const editor = CodeMirror.fromTextArea($('source'), {
  mode: 'stex', lineNumbers: true, lineWrapping: true, matchBrackets: true, autoCloseBrackets: true,
  indentUnit: 2, tabSize: 2, viewportMargin: 12,
  extraKeys: {
    'Ctrl-S': () => saveAll().catch(error => toast(error.message, true)), 'Cmd-S': () => saveAll().catch(error => toast(error.message, true)),
    'Ctrl-Enter': () => compile(), 'Cmd-Enter': () => compile(),
    'Ctrl-P': () => focusFiles(), 'Cmd-P': () => focusFiles(),
    'Ctrl-/': cm => toggleComment(cm), 'Cmd-/': cm => toggleComment(cm),
    Tab: cm => cm.somethingSelected() ? cm.indentSelection('add') : cm.replaceSelection('  ', 'end'),
    'Shift-Tab': cm => cm.indentSelection('subtract'),
  },
});
editor.setOption('readOnly', true);
const preview = new PDFPreview($('pdf-container'), { previous: $('previous-page'), next: $('next-page'), page: $('page-number'), total: $('page-total'), zoom: $('pdf-zoom'),
  onSource: navigateFromPDF, onError: error => toast(error.message, true) });
const comments = new SentenceComments({ editor, current: () => ({project: state.project?.id, path: state.current, doc: state.docs.get(state.current)}),
  save: saveDoc, open: openFile, toast });
const forwardButton = document.createElement('button');forwardButton.id = 'source-to-pdf';forwardButton.className = 'source-to-pdf';
forwardButton.textContent = 'PDF로 이동 ↗';forwardButton.title = '문장을 더블클릭해도 PDF의 해당 위치로 이동합니다';
document.querySelector('.editor-status').insertBefore(forwardButton, document.querySelector('.editor-status .status-spacer'));
forwardButton.addEventListener('click', () => navigateToPDF().catch(error => toast(error.message, true)));
editor.getWrapperElement().addEventListener('dblclick', event => {
  const doc = state.docs.get(state.current);
  if (!doc || !event.target.closest('.CodeMirror-line, .CodeMirror-line-like')) return;
  const position = editor.coordsChar({left: event.clientX, top: event.clientY}, 'window');
  const range = sentenceRange(doc.cm.getValue(), doc.cm.indexFromPos(position));
  editor.setSelection(doc.cm.posFromIndex(range.start), doc.cm.posFromIndex(range.end));
  navigateToPDF(position).catch(error => toast(error.message, true));
});
new ResizeObserver(() => editor.refresh()).observe($('editor-host'));

function toggleComment(cm) {
  const from = cm.getCursor('from').line, to = cm.getCursor('to').line;
  const lines = Array.from({ length: to - from + 1 }, (_, i) => from + i);
  const remove = lines.every(n => /^\s*%/.test(cm.getLine(n)));
  cm.operation(() => lines.forEach(n => {
    if (remove) { const index = cm.getLine(n).indexOf('%'); cm.replaceRange('', { line: n, ch: index }, { line: n, ch: index + (cm.getLine(n)[index + 1] === ' ' ? 2 : 1) }); }
    else cm.replaceRange('% ', { line: n, ch: 0 });
  }));
}
editor.on('change', cm => {
  const doc = state.docs.get(state.current);
  if (!doc || cm.getDoc() !== doc.cm) return;
  doc.dirty = doc.cm.getValue() !== doc.savedContent;
  doc.saveError = null;
  rememberDraft(doc); updateSaveState(); renderTabs(); updateCursor();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveDoc(doc).catch(error => toast(error.message, true)), 900);
  if ($('auto-compile').checked) scheduleCompile();
});
editor.on('cursorActivity', updateCursor);
function updateCursor() {
  const cursor = editor.getCursor(); $('cursor-position').textContent = `Ln ${cursor.line + 1}, Col ${cursor.ch + 1}`;
  const text = editor.getValue().replace(/%[^\n]*/g, '').replace(/\\[a-zA-Z]+/g, '');
  $('word-count').textContent = `${text.trim() ? text.trim().split(/\s+/).length.toLocaleString() : 0} words`;
  window.dispatchEvent(new Event('folio:editor-context'));
}
function updateSaveState() {
  const docs = [...state.docs.values()];
  const error = docs.some(d => d.saveError), saving = docs.some(d => d.saving), dirty = docs.some(d => d.dirty);
  $('save-state').className = 'save-state' + (error ? ' error' : dirty ? ' dirty' : '');
  $('save-state').innerHTML = icon(error ? 'warning' : 'check') + `<span>${error ? '저장 실패' : saving ? '저장 중…' : dirty ? '저장 대기…' : '모든 변경사항 저장됨'}</span>`;
}
async function saveDoc(doc) {
  if (doc.saving) { await doc.saving; if (doc.dirty) return saveDoc(doc); return; }
  if (!doc.dirty) return;
  const content = doc.cm.getValue();
  const promise = api('/api/file', { method: 'PUT', body: JSON.stringify({ project: doc.project, path: doc.path, content, revision: doc.revision }) });
  doc.saving = promise; updateSaveState();
  try {
    const result = await promise;
    doc.revision = result.revision; doc.savedContent = content; doc.saveError = null;
    doc.dirty = doc.cm.getValue() !== content;
    rememberDraft(doc);
  } catch (error) { doc.saveError = error; if (error.status === 409) showConflict(doc); throw error; }
  finally { doc.saving = null; updateSaveState(); renderTabs(); }
  if (doc.dirty) return saveDoc(doc);
  if (state.project?.id === doc.project) comments.refresh();
}
async function saveAll() {
  clearTimeout(saveTimer);
  await Promise.all([...state.docs.values()].filter(d => d.dirty || d.saving).map(saveDoc));
}

function renderTree() {
  const filter = $('file-search').value.trim().toLowerCase();
  $('file-tree').replaceChildren();
  function hasMatch(item) { return item.path.toLowerCase().includes(filter) || (item.children || []).some(hasMatch); }
  function render(items, depth = 0, parentMatches = false) {
    for (const item of items) {
      if (filter && !parentMatches && !hasMatch(item)) continue;
      const row = document.createElement('button');
      const folder = item.type === 'folder';
      const expanded = folder && (state.openFolders.has(item.path) || !!filter);
      row.className = `tree-item ${folder ? 'folder' : ''} ${expanded ? 'folder-open' : ''} ${state.current === item.path ? 'active' : ''}`;
      row.style.paddingLeft = `${9 + depth * 14}px`; row.title = item.path;
      row.dataset.path = item.path;
      if (folder) row.setAttribute('aria-expanded', String(expanded));
      if (state.current === item.path) row.setAttribute('aria-current', 'true');
      row.innerHTML = (folder ? '<svg class="icon chevron"><use href="#i-chevron"/></svg>' + icon('folder') : '<span style="width:2px"></span>' + fileSymbol(item.name)) + `<span class="file-name">${escapeHTML(item.name)}</span>` + (state.project.main === item.path ? '<span class="tree-main">main</span>' : '');
      row.addEventListener('click', () => {
        if (folder) {
          if (state.openFolders.has(item.path)) state.openFolders.delete(item.path); else state.openFolders.add(item.path);
          remember('folders:' + state.project.id, [...state.openFolders]); renderTree();
        } else { document.body.classList.remove('mobile-sidebar'); openFile(item.path).catch(error => toast(error.message, true)); }
      });
      $('file-tree').append(row);
      if (expanded) render(item.children, depth + 1, parentMatches || item.path.toLowerCase().includes(filter));
    }
  }
  render(state.tree);
  if (!$('file-tree').children.length) $('file-tree').innerHTML = '<div class="no-files">일치하는 파일이 없습니다.</div>';
  $('file-count').textContent = allFiles().length;
}
async function refreshTree() {
  if (!state.project) return;
  const key = state.project.id;
  const result = await api(query('/api/tree', { project: key }));
  if (state.project.id !== key) return;
  state.tree = result.tree; renderTree();
}
function renderTabs() {
  $('tabs').replaceChildren();
  for (const [path, doc] of state.docs) {
    const tab = document.createElement('div'); tab.className = 'tab' + (state.current === path ? ' active' : '');
    tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(state.current === path)); tab.tabIndex = 0; tab.title = path;
    tab.innerHTML = fileSymbol(path) + `<span class="tab-name">${escapeHTML(path.split('/').pop())}</span>` + (doc.dirty ? '<span class="tab-dirty" title="저장 대기"></span>' : '') + '<button class="icon-button" aria-label="파일 탭 닫기">' + icon('close') + '</button>';
    tab.addEventListener('click', () => openFile(path).catch(error => toast(error.message, true)));
    tab.addEventListener('keydown', event => { if (event.key === 'Enter') openFile(path).catch(error => toast(error.message, true)); });
    tab.querySelector('button').addEventListener('click', event => { event.stopPropagation(); closeFile(path).catch(error => toast(error.message, true)); });
    $('tabs').append(tab);
  }
}
let openSequence = 0;
async function openFile(path, line) {
  const project = state.project.id;
  const sequence = ++openSequence;
  const entry = allFiles().find(file => file.path === path);
  if (!entry) throw new Error('프로젝트에서 파일을 찾을 수 없습니다. 파일 목록을 새로고침해 주세요.');
  if (!entry.editable) {
    state.current = path; showAsset(entry); renderTree(); renderTabs(); return;
  }
  let doc = state.docs.get(path);
  if (!doc) {
    const result = await api(query('/api/file', { project, path }));
    if (state.project.id !== project || sequence !== openSequence) return;
    const draft = recall(`draft:${project}:${path}`, null);
    const recovered = draft && draft.revision === result.revision && draft.content !== result.content;
    const content = recovered ? draft.content : result.content;
    doc = { project, path, revision: result.revision, savedContent: result.content, cm: new CodeMirror.Doc(content, path.endsWith('.tex') || path.endsWith('.sty') ? 'stex' : null), dirty: !!recovered };
    state.docs.set(path, doc);
    if (recovered) { toast('이전에 저장하지 못한 편집 내용을 복구했습니다.'); setTimeout(() => saveDoc(doc).catch(error => toast(error.message, true)), 1200); }
    else if (draft && draft.content !== result.content) toast('외부 변경으로 이전 임시 편집본을 자동 적용하지 않았습니다. 브라우저 저장소에 보관되어 있습니다.', true);
  }
  if (state.project.id !== project || sequence !== openSequence) return;
  state.current = path;
  $('asset-preview').hidden = true; $('editor-host').hidden = false;
  editor.swapDoc(doc.cm); editor.setOption('readOnly', false); editor.refresh();
  $('breadcrumb').textContent = path.replaceAll('/', '  /  ');
  const language = path.endsWith('.bib') ? 'BibTeX' : path.endsWith('.tex') || path.endsWith('.sty') ? 'LaTeX' : 'Plain text';
  $('editor-language').textContent = language; $('editor-mode').textContent = language;
  remember('file:' + project, path);
  const parts = path.split('/'); parts.pop(); let parent = '';
  for (const part of parts) { parent += (parent ? '/' : '') + part; state.openFolders.add(parent); }
  renderTabs(); renderTree(); updateSaveState(); updateCursor();
  comments.render();
  if (line) { editor.setCursor(Math.max(0, line - 1), 0); editor.scrollIntoView({ line: Math.max(0, line - 1), ch: 0 }, 100); editor.focus(); }
}

let sourceJumpSequence = 0, sourceHighlight;
let pdfJumpSequence = 0;
async function navigateToPDF(position = editor.getCursor()) {
  const project = state.project?.id, path = state.current, doc = state.docs.get(path);
  if (!doc || !path.endsWith('.tex')) throw new Error('LaTeX 본문 파일에서 문장을 선택해 주세요.');
  const sequence = ++pdfJumpSequence;
  if (!preview.documentURL) throw new Error('PDF를 먼저 컴파일하고 불러온 뒤 이동해 주세요.');
  const version = new URL(preview.documentURL, location.href).searchParams.get('v');
  const target = await api('/api/pdf-location', {method: 'POST', body: JSON.stringify({project, path, version,
    line: position.line + 1, column: position.ch + 1, content: doc.cm.getValue()})});
  if (sequence !== pdfJumpSequence || state.project?.id !== project || state.current !== path || new URL(preview.documentURL, location.href).searchParams.get('v') !== version) return;
  await preview.reveal(target);
  toast(`PDF ${target.page}페이지의 해당 위치로 이동했습니다.${target.source_changed ? ' 마지막 컴파일 기준입니다.' : ''}`);
}
function sourceTarget(doc, location, text = '', word = '') {
  const lines = doc.cm.getValue().split('\n');
  let line = Math.min(Math.max(0, location.line - 1), lines.length - 1);
  // Recover the same line if unsaved edits have inserted lines before it.
  const matches = lines.map((content, index) => content === location.line_text ? index : -1).filter(index => index >= 0);
  if (matches.length) line = matches.reduce((best, index) => Math.abs(index - line) < Math.abs(best - line) ? index : best);
  let ch = Math.min(Math.max(0, location.column - 1), lines[line].length);
  // SyncTeX can point to the end of a paragraph. Use clicked text to refine that location.
  for (const fragment of [text, word]) {
    const phrase = fragment.trim().replaceAll('\u00ad', '');
    if (phrase.length < 3 || phrase.length > 1500) continue;
    const nearby = [];
    for (let index = Math.max(0, line - 12); index <= Math.min(lines.length - 1, line + 12); index++) {
      const column = lines[index].indexOf(phrase);
      if (column >= 0) nearby.push({ line: index, ch: column });
    }
    if (nearby.length) return nearby.reduce((best, item) => Math.abs(item.line - line) < Math.abs(best.line - line) ? item : best);
  }
  return { line, ch };
}
async function openSourceLocation(location, position = {}) {
  let project = state.projects.find(item => item.id === location.project);
  if (!project) throw new Error('연결된 프로젝트를 찾을 수 없습니다. 프로젝트 목록을 새로고침해 주세요.');
  if (state.project?.id !== project.id) await selectProject(project);
  if (state.project?.id !== project.id) throw new Error('프로젝트를 전환한 뒤 다시 더블클릭해 주세요.');
  $('file-search').value = '';
  if (!allFiles().some(file => file.path === location.path)) await refreshTree();
  await openFile(location.path);
  if (state.current !== location.path || state.project?.id !== project.id) return;
  const doc = state.docs.get(location.path);
  if (!doc) throw new Error('이 소스 파일은 편집기에서 열 수 없습니다.');
  const target = sourceTarget(doc, location, position.text, position.word);
  if (sourceHighlight) { clearTimeout(sourceHighlight.timer); sourceHighlight.doc.removeLineClass(sourceHighlight.handle, 'background', 'source-jump-line'); }
  document.body.classList.remove('mobile-sidebar'); $('logs-panel').hidden = true;
  editor.setCursor(target); editor.scrollIntoView(target, 120); editor.focus();
  const handle = doc.cm.addLineClass(target.line, 'background', 'source-jump-line');
  sourceHighlight = { doc: doc.cm, handle, timer: setTimeout(() => doc.cm.removeLineClass(handle, 'background', 'source-jump-line'), 3500) };
  $('file-tree').querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' });
  toast(`${location.path} · ${target.line + 1}행으로 이동했습니다.`);
  return { path: location.path, line: target.line + 1 };
}
async function navigateFromPDF(position) {
  const sequence = ++sourceJumpSequence;
  const { project, version, page, x, y } = position;
  const location = await api(query('/api/source-location', { project, version, page, x, y, text: (position.text || '').slice(0, 1500), word: (position.word || '').slice(0, 100) }));
  if (sequence !== sourceJumpSequence) return;
  return openSourceLocation(location, position);
}
window.addEventListener('message', async event => {
  if (event.origin !== location.origin || event.data?.type !== 'folio:source-jump' || !event.source || event.source.opener !== window) return;
  const source = event.source, request = event.data.request;
  try {
    const result = await navigateFromPDF(event.data.position);
    source.postMessage({ type: 'folio:source-opened', request, result }, location.origin);
  } catch (error) {
    toast(error.message, true);
    source.postMessage({ type: 'folio:source-opened', request, error: error.message }, location.origin);
  }
});
function showAsset(entry) {
  $('editor-host').hidden = true; $('asset-preview').hidden = false;
  $('asset-preview').replaceChildren();
  const url = query('/api/asset', { project: state.project.id, path: entry.path });
  if (entry.path.toLowerCase().endsWith('.pdf')) {
    const frame = document.createElement('iframe'); frame.src = url; frame.title = entry.path; $('asset-preview').append(frame);
  } else if (/\.(png|jpe?g|webp|gif)$/i.test(entry.path)) {
    const image = document.createElement('img'); image.src = url; image.alt = entry.path; $('asset-preview').append(image);
  } else { const message = document.createElement('p'); message.textContent = '이 파일 형식은 미리보기를 지원하지 않습니다.'; $('asset-preview').append(message); }
  $('breadcrumb').textContent = entry.path; $('editor-language').textContent = 'Preview';
  window.dispatchEvent(new Event('folio:editor-context'));
}
async function closeFile(path) {
  const doc = state.docs.get(path); if (doc) await saveDoc(doc);
  state.docs.delete(path);
  if (state.current === path) {
    const next = [...state.docs.keys()].pop();
    if (next) await openFile(next);
    else { state.current = null; editor.swapDoc(new CodeMirror.Doc('', 'stex')); editor.setOption('readOnly', true); $('breadcrumb').textContent = '왼쪽에서 파일을 선택해 주세요'; }
  }
  renderTabs(); renderTree(); updateSaveState();
  comments.render();
}

async function selectProject(project) {
  if (state.switching) return;
  state.switching = true;
  try {
    await saveAll(); clearTimeout(autoTimer); clearTimeout(saveTimer);
    preview.clear();
    $('pdf-container').innerHTML = '<div class="pdf-empty"><div class="empty-document">' + icon('file') + '</div><h2>글이 논문이 되는 곳</h2><p>컴파일하면 완성된 PDF가 여기에 표시됩니다.</p><span>Ctrl / ⌘ + Enter로 컴파일</span></div>';
    state.project = project; state.docs.clear(); state.current = null; state.job = null; state.compileAgain = false; openSequence++;
    comments.reset();++pdfJumpSequence;
    state.openFolders = new Set(recall('folders:' + project.id, []));
    $('project-title').textContent = project.title; $('project-picker').title = project.name;
    $('root-label').textContent = project.name.replaceAll('_', ' '); $('root-label').title = project.name;
    $('footer-project').textContent = project.name; $('workspace-path').textContent = 'Workspace · ' + state.workspace;
    $('main-label').textContent = 'Main · ' + project.main;
    $('main-select').replaceChildren(...project.mains.map(main => new Option(main, main, false, main === project.main)));
    $('engine-select').value = project.engine;
    $('logs-panel').hidden = true; $('error-badge').hidden = true; $('diagnostics').replaceChildren(); $('raw-log').textContent = '';
    $('preview-state').textContent = project.pdf_version ? '최신 PDF' : '컴파일 대기';
    $('compile-detail').textContent = project.compiled_at ? `마지막 컴파일 ${new Date(project.compiled_at * 1000).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}` : '컴파일하면 PDF가 표시됩니다';
    remember('project', project.id); renderTabs(); updateCompileButton();
    await refreshTree();
    const saved = recall('file:' + project.id, null);
    const initial = allFiles().some(file => file.path === saved) ? saved : (allFiles().some(file => file.path === '1_introduction.tex') ? '1_introduction.tex' : project.main);
    if (initial) await openFile(initial);
    comments.refresh();
    updatePDFLinks();
    if (project.pdf_version) await loadPDF(project.pdf_version);
    if (project.active_job) { state.job = { id: project.active_job, project: project.id, status: 'running' }; updateCompileButton(); pollJob(state.job); }
    else if (!project.pdf_version && project.main) setTimeout(() => { if (state.project.id === project.id) compile(); }, 350);
  } finally { state.switching = false; }
}
function renderProjects() {
  $('project-list').replaceChildren();
  for (const project of state.projects) {
    const button = document.createElement('button'); button.className = 'project-card' + (project.id === state.project?.id ? ' selected' : '');
    button.innerHTML = icon('folder') + `<div><strong>${escapeHTML(project.title)}</strong><small>${escapeHTML(project.name)}</small><small>${project.mains.length} main document${project.mains.length !== 1 ? 's' : ''} · ${escapeHTML(project.engine)}</small></div>` + (project.id === state.project?.id ? icon('check') : '');
    button.addEventListener('click', () => { $('project-dialog').close(); if (project.id !== state.project?.id) selectProject(project).catch(error => toast(error.message, true)); });
    $('project-list').append(button);
  }
}
function updatePDFLinks() {
  if (!state.project) return;
  $('download').href = query('/api/pdf', { project: state.project.id, download: '1' });
}
async function loadPDF(version) {
  const project = state.project.id;
  try { await preview.load(query('/api/pdf', { project, v: version })); }
  catch (error) { if (state.project.id === project) toast('PDF를 표시하지 못했습니다: ' + error.message, true); }
}
function updateCompileButton() {
  const running = state.job?.status === 'running';
  $('compile').classList.toggle('compiling', running);
  $('compile').innerHTML = icon(running ? 'stop' : 'play') + `<span>${running ? '컴파일 중' : '컴파일'}</span><kbd>${/Mac/.test(navigator.platform) ? '⌘' : 'Ctrl'} ↵</kbd>`;
  $('compile').title = running ? '컴파일 중지' : '전체 문서 컴파일 · Ctrl/⌘ + Enter';
}
function scheduleCompile() { clearTimeout(autoTimer); autoTimer = setTimeout(() => compile(true), 2200); }
async function compile(automatic = false) {
  clearTimeout(autoTimer);
  if (!state.project) return;
  if (state.job?.status === 'running') {
    if (automatic) { state.compileAgain = true; return; }
    try { await api('/api/cancel', { method: 'POST', body: JSON.stringify({ id: state.job.id }) }); } catch (error) { toast(error.message, true); }
    return;
  }
  const project = state.project;
  $('compile').disabled = true;
  try {
    await saveAll();
    if (state.project.id !== project.id) return;
    const job = await api('/api/compile', { method: 'POST', body: JSON.stringify({ project: project.id, main: project.main, engine: project.engine }) });
    state.job = job; $('preview-state').textContent = '컴파일 중…'; $('compile-detail').textContent = `${project.engine} · ${project.main}`; updateCompileButton();
    pollJob(job);
  } catch (error) { toast(error.message, true); }
  finally { $('compile').disabled = false; }
}
async function pollJob(initial) {
  let job = initial;
  while (state.project?.id === initial.project && state.job?.id === initial.id) {
    try {
      job = await api(query('/api/job', { id: initial.id }));
      if (state.project?.id !== initial.project || state.job?.id !== initial.id) return;
      state.job = job; $('raw-log').textContent = job.log || '';
      if (job.status !== 'running') break;
    } catch (error) { toast(error.message, true); state.job = null; updateCompileButton(); return; }
    await new Promise(resolve => setTimeout(resolve, 700));
  }
  if (state.project?.id !== initial.project || state.job?.id !== initial.id) return;
  updateCompileButton(); renderDiagnostics(job);
  const count = job.errors?.length || 0; $('error-badge').textContent = count; $('error-badge').hidden = !count;
  if (job.status === 'success') {
    $('preview-state').textContent = '최신 PDF';
    $('compile-detail').textContent = `${job.duration}초에 컴파일 완료${job.warnings.length ? ` · 경고 ${job.warnings.length}` : ''}`;
    state.project.pdf_version = job.pdf_version; state.project.compiled_at = job.finished_at;
    await loadPDF(job.pdf_version);
    channel?.postMessage({ project: initial.project, version: job.pdf_version });
  } else if (job.status === 'cancelled') {
    $('preview-state').textContent = '컴파일 중지됨'; $('compile-detail').textContent = '컴파일을 중지했습니다';
  } else {
    $('preview-state').textContent = state.project.pdf_version ? '이전 PDF · 컴파일 오류' : '컴파일 오류';
    $('compile-detail').textContent = `컴파일 실패 · 오류 ${count}`; $('logs-panel').hidden = false; setLogTab(false);
  }
  if (state.compileAgain) { state.compileAgain = false; compile(true); }
}
function renderDiagnostics(job) {
  $('diagnostics').replaceChildren();
  const items = [...(job.errors || []).map(item => ({ ...item, type: 'error' })), ...(job.warnings || []).map(item => ({ ...item, type: 'warning' }))];
  if (!items.length) items.push({ message: job.status === 'success' ? '컴파일이 완료되었습니다. 오류가 없습니다.' : job.status === 'cancelled' ? '컴파일이 중지되었습니다.' : '컴파일 로그를 확인해 주세요.', type: 'success' });
  for (const item of items) {
    const row = document.createElement('div'); row.className = 'diagnostic ' + item.type;
    row.innerHTML = icon(item.type === 'success' ? 'check' : 'warning') + '<div></div>';
    const detail = row.lastElementChild;
    if (item.file) {
      const link = document.createElement('button'); link.textContent = `${item.file}:${item.line}`;
      link.addEventListener('click', () => openFile(item.file, item.line).catch(error => toast(error.message, true))); detail.append(link);
    }
    const message = document.createElement('p'); message.textContent = item.message; detail.append(message); $('diagnostics').append(row);
  }
}
function setLogTab(raw) { $('raw-log').hidden = !raw; $('diagnostics').hidden = raw; $('raw-log-tab').classList.toggle('active', raw); $('diagnostics-tab').classList.toggle('active', !raw); }
function focusFiles() { if (window.innerWidth <= 640) document.body.classList.add('mobile-sidebar'); else document.body.classList.remove('sidebar-hidden'); $('file-search').focus(); $('file-search').select(); }

let conflictingDoc;
function showConflict(doc) {
  conflictingDoc = doc; $('conflict-file').textContent = doc.path;
  if (!$('conflict-dialog').open) $('conflict-dialog').showModal();
}
$('download-draft').addEventListener('click', () => {
  if (!conflictingDoc) return;
  const url = URL.createObjectURL(new Blob([conflictingDoc.cm.getValue()], { type: 'text/plain;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = conflictingDoc.path.split('/').pop(); anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
});
$('reload-conflict').addEventListener('click', async () => {
  const doc = conflictingDoc; if (!doc) return;
  try {
    const result = await api(query('/api/file', { project: doc.project, path: doc.path }));
    remember(`recovered:${doc.project}:${doc.path}:${Date.now()}`, doc.cm.getValue());
    Object.assign(doc, { cm: new CodeMirror.Doc(result.content, /\.(tex|sty)$/.test(doc.path) ? 'stex' : null), revision: result.revision, savedContent: result.content, dirty: false, saveError: null });
    rememberDraft(doc);
    if (state.current === doc.path) { editor.swapDoc(doc.cm); editor.refresh(); updateCursor(); }
    renderTabs(); updateSaveState(); $('conflict-dialog').close(); toast('디스크의 최신 파일을 불러왔습니다.');
  } catch (error) { toast(error.message, true); }
});

$('file-search').addEventListener('input', renderTree);
$('refresh-tree').addEventListener('click', () => refreshTree().catch(error => toast(error.message, true)));
$('compile').addEventListener('click', () => compile());
$('editor-find').addEventListener('click', () => { editor.focus(); CodeMirror.commands.find(editor); });
$('project-picker').addEventListener('click', async () => {
  try { const result = await api('/api/projects'); state.projects = result.projects; renderProjects(); $('project-dialog').showModal(); } catch (error) { toast(error.message, true); }
});
$('settings-button').addEventListener('click', () => $('settings-dialog').showModal());
$('apply-settings').addEventListener('click', () => { state.project.main = $('main-select').value; state.project.engine = $('engine-select').value; $('main-label').textContent = 'Main · ' + state.project.main; $('settings-dialog').close(); renderTree(); if (state.job?.status === 'running') { state.compileAgain = true; toast('진행 중인 컴파일이 끝나면 새 설정을 적용합니다.'); } else compile(); });
$('new-file').addEventListener('click', () => { $('entry-error').textContent = ''; $('entry-path').value = ''; $('new-dialog').showModal(); $('entry-path').focus(); });
$('new-form').addEventListener('submit', async event => {
  event.preventDefault(); const path = $('entry-path').value.trim(); const kind = $('entry-kind').value;
  try { await api('/api/entry', { method: 'POST', body: JSON.stringify({ project: state.project.id, path, kind }) }); await refreshTree(); $('new-dialog').close(); if (kind === 'file') await openFile(path); else { state.openFolders.add(path); renderTree(); } }
  catch (error) { $('entry-error').textContent = error.message; }
});
$('show-logs').addEventListener('click', () => $('logs-panel').hidden = !$('logs-panel').hidden);
$('close-logs').addEventListener('click', () => $('logs-panel').hidden = true);
$('raw-log-tab').addEventListener('click', () => setLogTab(true)); $('diagnostics-tab').addEventListener('click', () => setLogTab(false));
$('shortcuts').addEventListener('click', () => $('shortcuts-dialog').showModal());
$('toggle-sidebar').addEventListener('click', () => document.body.classList.toggle(window.innerWidth <= 640 ? 'mobile-sidebar' : 'sidebar-hidden'));
$('popout').addEventListener('click', () => {
  if (!state.project) return;
  const popup = window.open(query('/viewer', { project: state.project.id }), 'folio-pdf-' + state.project.id, 'popup,width=950,height=1000');
  if (!popup) toast('브라우저에서 팝업을 허용해 주세요.', true);
});
$('auto-compile').checked = recall('auto-compile', false);
$('auto-compile').addEventListener('change', () => { remember('auto-compile', $('auto-compile').checked); if ($('auto-compile').checked) scheduleCompile(); else { clearTimeout(autoTimer); state.compileAgain = false; } });
document.querySelectorAll('.dialog-close').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } }));
window.addEventListener('keydown', event => {
  if (event.defaultPrevented) return;
  if (!(event.ctrlKey || event.metaKey)) return;
  if (['s', 'p', 'Enter'].includes(event.key)) {
    event.preventDefault(); if (event.key === 's') saveAll().catch(error => toast(error.message, true)); else if (event.key === 'p') focusFiles(); else compile();
  }
});
window.addEventListener('beforeunload', event => { if ([...state.docs.values()].some(doc => doc.dirty || doc.saving)) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('visibilitychange', () => { if (document.hidden) saveAll().catch(() => {}); });

// Drag with pointer capture so the panes remain responsive over the PDF canvas.
function setupResizer(selector, kind) {
  const handle = document.querySelector(selector); let dragging = false;
  handle.addEventListener('pointerdown', event => { dragging = true; handle.classList.add('dragging'); handle.setPointerCapture(event.pointerId); document.body.style.userSelect = 'none'; });
  handle.addEventListener('pointermove', event => {
    if (!dragging) return;
    if (kind === 'sidebar') {
      const width = Math.min(420, Math.max(180, event.clientX)); document.documentElement.style.setProperty('--sidebar-width', width + 'px'); remember('sidebar-width', width);
    } else {
      const left = $('editor-panel').getBoundingClientRect().left; const available = window.innerWidth - left - 5;
      const editorWidth = Math.max(270, Math.min(available - 300, event.clientX - left));
      const ratio = editorWidth / Math.max(300, available - editorWidth); document.documentElement.style.setProperty('--editor-fr', ratio); remember('editor-fr', ratio);
    }
  });
  const stop = () => { dragging = false; handle.classList.remove('dragging'); document.body.style.userSelect = ''; editor.refresh(); };
  handle.addEventListener('pointerup', stop); handle.addEventListener('lostpointercapture', stop);
  handle.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return; event.preventDefault();
    const delta = event.key === 'ArrowRight' ? 20 : -20;
    if (kind === 'sidebar') { const width = Math.min(420, Math.max(180, $('sidebar').clientWidth + delta)); document.documentElement.style.setProperty('--sidebar-width', width + 'px'); remember('sidebar-width', width); }
    else { const ratio = Math.min(3, Math.max(0.4, parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--editor-fr')) + delta / 100)); document.documentElement.style.setProperty('--editor-fr', ratio); remember('editor-fr', ratio); }
  });
}
setupResizer('.sidebar-resizer', 'sidebar'); setupResizer('.editor-resizer', 'editor');
const width = recall('sidebar-width', null); if (width && window.innerWidth > 800) document.documentElement.style.setProperty('--sidebar-width', width + 'px');
const ratio = recall('editor-fr', null); if (ratio) document.documentElement.style.setProperty('--editor-fr', ratio);

async function boot() {
  try {
    const result = await api('/api/projects'); state.projects = result.projects; state.workspace = result.workspace;
    $('engine-select').replaceChildren(...result.engines.map(engine => new Option(({ pdflatex: 'pdfLaTeX', lualatex: 'LuaLaTeX', xelatex: 'XeLaTeX' })[engine], engine)));
    if (!state.projects.length) { $('project-title').textContent = 'LaTeX 프로젝트가 없습니다'; $('file-tree').innerHTML = '<div class="no-files">작업 폴더 안에 .tex 파일을 포함한 프로젝트 폴더를 넣고 새로고침해 주세요.</div>'; $('compile').disabled = true; return; }
    const parameters = new URLSearchParams(location.search);
    const saved = parameters.get('project') || recall('project', null); await selectProject(state.projects.find(project => project.id === saved) || state.projects[0]);
    document.title = state.project.title + ' — Folio';
    if (parameters.has('sourcePage')) await navigateFromPDF({ project: state.project.id, version: parameters.get('sourceVersion'),
      page: parameters.get('sourcePage'), x: parameters.get('sourceX'), y: parameters.get('sourceY'), text: parameters.get('sourceText') || '', word: parameters.get('sourceWord') || '' });
  } catch (error) { toast(error.message, true); $('project-title').textContent = '프로젝트 연결 오류'; }
}
window.folioAssistant = {
  getContext() {
    const doc = state.docs.get(state.current);
    const selection = doc ? editor.getSelection() : '';
    return { project: state.project?.id, project_title: state.project?.title, path: state.current,
      content: doc?.cm.getValue() || '', cursor_line: doc ? editor.getCursor().line + 1 : 1,
      selection: { text: selection, start_line: doc ? editor.getCursor('from').line + 1 : 1,
        end_line: doc ? editor.getCursor('to').line + 1 : 1 } };
  },
  async applyEdit(proposal) {
    if (state.project?.id !== proposal.project) throw new Error('수정안의 프로젝트를 먼저 열어 주세요.');
    await openFile(proposal.path);
    const doc = state.docs.get(proposal.path);
    if (!doc || state.current !== proposal.path || state.project?.id !== proposal.project) throw new Error('파일을 열지 못했습니다. 다시 시도해 주세요.');
    const content = doc.cm.getValue(), at = content.indexOf(proposal.old_text);
    if (at < 0 || content.indexOf(proposal.old_text, at + 1) >= 0) throw new Error('원문이 변경되었거나 여러 번 나타납니다. 현재 내용을 기준으로 AI에게 다시 요청해 주세요.');
    doc.cm.replaceRange(proposal.new_text, doc.cm.posFromIndex(at), doc.cm.posFromIndex(at + proposal.old_text.length), '+ai');
    await saveDoc(doc);
    editor.setCursor(doc.cm.posFromIndex(at)); editor.scrollIntoView(editor.getCursor(), 100);
    return { saved: true };
  },
};
boot();

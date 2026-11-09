import { PDFPreview } from '/pdf-preview.js';
const $ = id => document.getElementById(id);
const project = new URLSearchParams(location.search).get('project');
const preview = new PDFPreview($('container'), { previous: $('previous'), next: $('next'), page: $('page'), total: $('total'), zoom: $('zoom'),
  onSource: openSource, onError: error => { $('state').textContent = error.message; } });
let version = null, refreshing = false;
const pendingJumps = new Map();
async function openSource(position) {
  if (window.opener && !window.opener.closed) {
    $('state').textContent = 'LaTeX 위치 찾는 중…';
    const request = crypto.randomUUID();
    const response = new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pendingJumps.delete(request); reject(new Error('편집기에서 페이지를 새로고침한 뒤 다시 시도해 주세요.')); }, 12000);
      pendingJumps.set(request, { resolve, reject, timer });
    });
    window.opener.postMessage({ type: 'folio:source-jump', request, position }, location.origin);
    const result = await response;
    if (result) $('state').textContent = `${result.path} · ${result.line}행`;
    window.opener.focus();
  } else {
    const parameters = new URLSearchParams({ project, sourceVersion: position.version, sourcePage: position.page, sourceX: position.x, sourceY: position.y,
      sourceText: (position.text || '').slice(0, 1500), sourceWord: (position.word || '').slice(0, 100) });
    const editor = window.open('/?' + parameters, 'folio-editor-' + project);
    $('state').textContent = editor ? '편집기에서 소스 위치를 엽니다' : '브라우저에서 팝업을 허용해 주세요';
  }
}
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== window.opener || event.data?.type !== 'folio:source-opened') return;
  const pending = pendingJumps.get(event.data.request);
  if (!pending) return;
  clearTimeout(pending.timer); pendingJumps.delete(event.data.request);
  if (event.data.error) pending.reject(new Error(event.data.error)); else pending.resolve(event.data.result);
});
$('download').href = '/api/pdf?' + new URLSearchParams({ project, download: '1' });
async function refresh() {
  if (!project || refreshing) return;
  refreshing = true;
  try {
    const response = await fetch('/api/latest?' + new URLSearchParams({ project }));
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    if (result.version && result.version !== version) {
      await preview.load('/api/pdf?' + new URLSearchParams({ project, v: result.version }));
      version = result.version; $('title').textContent = result.main || 'PDF 미리보기';
      $('state').textContent = '최신 PDF · 자동 갱신';
    } else if (!result.version) $('state').textContent = '컴파일 대기';
  } catch (error) { $('state').textContent = '연결 오류 · 재시도 중'; }
  finally { refreshing = false; }
}
if ('BroadcastChannel' in window) { const channel = new BroadcastChannel('folio-pdf'); channel.onmessage = event => { if (event.data.project === project) refresh(); }; }
refresh(); setInterval(refresh, 2500);

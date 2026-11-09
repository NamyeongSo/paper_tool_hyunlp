const $ = id => document.getElementById(id);
const root = $('ai-assistant');
const state = { project: null, session: null, run: null, busy: false, epoch: 0, settings: null, applying: null, feed: null };
const icon = name => `<svg class="icon"><use href="#i-${name}"/></svg>`;
root.innerHTML = `<div class="ai-heading"><span class="ai-spark">✦</span><strong>AI assistant</strong><span class="ai-react-label">ReAct</span><span class="ai-heading-spacer"></span><button id="ai-new" class="icon-button" title="새 대화" aria-label="AI 새 대화">${icon('plus')}</button><button id="ai-settings" class="icon-button" title="모델·문맥 설정" aria-label="AI 설정">${icon('settings')}</button><button id="ai-toggle" class="icon-button" title="AI 패널 접기" aria-label="AI 패널 접기/펼치기">${icon('down')}</button></div>
<div class="ai-content"><div class="ai-context-bar"><span class="dot"></span><span id="ai-context">현재 파일을 선택해 주세요</span><span id="ai-model" title="모델 연결 확인 중">연결 확인 중…</span></div><div id="ai-messages" class="ai-messages" role="log" aria-label="AI 대화"></div><div id="ai-progress" class="ai-progress" hidden><span class="ai-pulse"></span><span id="ai-stage">준비 중…</span></div><div id="ai-error" class="ai-error" role="status" hidden></div><div class="ai-compose"><textarea id="ai-input" rows="2" maxlength="20000" placeholder="이 문장을 더 명확하게 고쳐줘…" aria-label="AI에게 교정 요청"></textarea><button id="ai-send" class="ai-send" title="보내기" aria-label="AI 메시지 보내기">↑</button></div><div class="ai-bottom"><span id="ai-memory">현재 파일 자동 첨부</span><span>Enter 전송 · Shift+Enter 줄바꿈</span></div></div>`;
const dialog = document.createElement('dialog');
dialog.id = 'ai-settings-dialog';
dialog.innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">LOCAL VLLM · REACT AGENT</span><h2>AI 설정</h2></div><button id="ai-settings-close" class="icon-button" aria-label="AI 설정 닫기">${icon('close')}</button></div><form id="ai-settings-form"><div class="ai-settings-grid"><label class="field">vLLM 포트<input id="ai-port" type="number" min="1" max="65535" required></label><label class="field">문맥 길이 (0 = 서버 자동)<input id="ai-window" type="number" min="0" step="1" required></label></div><label class="field">모델 이름<input id="ai-model-input" required autocomplete="off"></label><div class="ai-settings-grid"><label class="field">최대 출력 토큰<input id="ai-output" type="number" min="128" max="32768" required></label><label class="field">자동 압축 시작 (%)<input id="ai-threshold" type="number" min="50" max="95" required></label></div><p id="ai-connection" class="field-help"></p><p class="field-help">이전 대화는 요약하고, 전체 채팅 기록은 보관합니다. 현재 파일이 너무 크면 커서 주변을 전달하고 도구로 필요한 부분을 읽습니다.</p><div class="ai-config-path"><span>수정 가능한 시스템 프롬프트</span><code id="ai-prompt-path"></code><span>서버 설정 파일</span><code id="ai-config-path"></code></div><p id="ai-settings-error" class="form-error"></p><button class="button primary full-width" type="submit">저장하고 연결 확인</button></form>`;
document.body.append(dialog);
const streamLabel = document.createElement('label');
streamLabel.className = 'ai-stream-field';
streamLabel.innerHTML = '<input id="ai-stream" type="checkbox"> 모델 응답 스트리밍';
$('ai-settings-form').insertBefore(streamLabel, $('ai-connection'));
const thinkingFields = document.createElement('div');
thinkingFields.className = 'ai-thinking-settings';
thinkingFields.innerHTML = '<label class="ai-stream-field"><input id="ai-thinking" type="checkbox"> Thinking 사용</label><div class="ai-settings-grid"><label class="field">Thinking 정도<select id="ai-thinking-level"><option value="low">낮음</option><option value="medium">보통</option><option value="high">높음</option><option value="custom">직접 지정</option></select></label><label id="ai-thinking-custom-field" class="field" hidden>추론 토큰 상한<input id="ai-thinking-budget" type="number" min="1" max="32768" step="1"></label></div><p id="ai-thinking-help" class="field-help"></p>';
$('ai-settings-form').insertBefore(thinkingFields, $('ai-connection'));
$('ai-output').closest('label').firstChild.textContent = '전체 출력 한도 (추론 + 답변)';
dialog.querySelector('.eyebrow').textContent = 'MODEL CONNECTION · REACT AGENT';
const providerField = document.createElement('label'); providerField.className = 'field';
providerField.innerHTML = 'API 제공자<select id="ai-provider"><option value="local">Localhost · vLLM</option><option value="openai">OpenAI</option><option value="exaone">EXAONE · K-EXAONE 2.5</option></select>';
$('ai-settings-form').prepend(providerField);
const openaiFields = document.createElement('div'); openaiFields.id = 'ai-openai-fields'; openaiFields.hidden = true;
openaiFields.innerHTML = '<label class="field"><span id="ai-url-label">OpenAI API URL</span><input id="ai-base-url" type="url" placeholder="https://api.openai.com/v1" autocomplete="off"></label><label class="field"><span id="ai-key-label">OpenAI API 키</span><input id="ai-api-key" type="password" autocomplete="off" placeholder="서버의 OPENAI_API_KEY도 사용할 수 있습니다"></label><p id="ai-key-help" class="field-help"></p><label class="ai-stream-field"><input id="ai-clear-key" type="checkbox"> 서버에 저장한 API 키 삭제</label>';
$('ai-settings-form').insertBefore(openaiFields, $('ai-model-input').closest('label'));
const providerHelp = document.createElement('p'); providerHelp.id = 'ai-provider-help'; providerHelp.className = 'field-help';
$('ai-settings-form').insertBefore(providerHelp, $('ai-connection'));
let displayedProvider = 'local';
let providerDrafts = {};
function renderProviderFields(provider, model, level) {
  const cloud = provider !== 'local', exaone = provider === 'exaone', config = state.settings?.config || {};
  displayedProvider = provider;
  $('ai-openai-fields').hidden = !cloud;
  $('ai-port').closest('label').hidden = cloud;
  $('ai-port').disabled = cloud;
  for (const field of openaiFields.querySelectorAll('input')) field.disabled = !cloud;
  $('ai-base-url').required = cloud;
  $('ai-base-url').readOnly = exaone;
  $('ai-base-url').value = exaone ? config.exaone_base_url || 'https://api.exaone.ai/v1' : providerDrafts.openai?.baseURL || config.openai_base_url || 'https://api.openai.com/v1';
  $('ai-url-label').textContent = exaone ? 'EXAONE API URL' : 'OpenAI API URL';
  $('ai-key-label').textContent = exaone ? 'EXAONE API 키' : 'OpenAI API 키';
  const keyState = state.settings?.api_keys?.[provider] || (provider === config.provider ? {configured:state.settings?.api_key_configured, source:state.settings?.api_key_source} : {});
  $('ai-api-key').placeholder = keyState.configured ? '이미 설정됨 · 변경할 때만 입력' : `API 키 입력 또는 서버 ${exaone ? 'EXAONE_API_KEY' : 'OPENAI_API_KEY'} 사용`;
  $('ai-key-help').textContent = keyState.configured ? (keyState.source === 'environment' ? '서버 환경변수에 API 키가 설정되어 있습니다.' : '서버에 API 키가 저장되어 있습니다.') : 'API 키는 서버에서만 사용합니다. 등록한 키를 화면으로 다시 보내지 않습니다.';
  $('ai-model-input').value = model;
  $('ai-model-input').readOnly = exaone;
  $('ai-thinking').checked = exaone || (providerDrafts[provider]?.thinking ?? config.chat_template_kwargs?.enable_thinking === true);
  const options = exaone ? [['low','낮음'],['high','높음'],['max','최대']] : provider === 'openai' ? [['low','낮음'],['medium','보통'],['high','높음'],['xhigh','매우 높음'],['max','최대']] : [['low','낮음'],['medium','보통'],['high','높음'],['custom','직접 지정']];
  $('ai-thinking-level').replaceChildren(...options.map(([value, label]) => new Option(label, value)));
  $('ai-thinking-level').value = options.some(([value]) => value === level) ? level : exaone ? 'high' : 'medium';
  providerHelp.textContent = exaone ? '현재 파일과 대화가 EXAONE API로 전달됩니다. 저장 시 짧은 API 요청으로 키와 연결을 확인합니다. 새로고침 시에는 추가 호출하지 않습니다.' : '';
  updateThinkingControls();
}
$('ai-provider').addEventListener('change', () => {
  providerDrafts[displayedProvider] = { model: $('ai-model-input').value, level: $('ai-thinking-level').value, thinking: $('ai-thinking').checked, baseURL: $('ai-base-url').value };
  // Never carry a credential typed for one provider into a different provider.
  $('ai-api-key').value = ''; $('ai-clear-key').checked = false;
  const provider = $('ai-provider').value, config = state.settings?.config || {}, saved = providerDrafts[provider];
  const model = provider === 'exaone' ? config.exaone_model || 'k-exaone_v2.5' : provider === 'openai' ? config.openai_model || 'gpt-6-luna' : config.model || '';
  const level = provider === 'exaone' ? config.exaone_reasoning_effort || 'high' : provider === 'openai' ? config.openai_reasoning_effort : config.thinking_level;
  renderProviderFields(provider, saved?.model || model, saved?.level || level);
});

function node(tag, className, text) { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; }
function localGet(key) { try { return localStorage.getItem('folio:ai:' + key); } catch { return null; } }
function localSet(key, value) { try { if (value === null) localStorage.removeItem('folio:ai:' + key); else localStorage.setItem('folio:ai:' + key, value); } catch {} }
async function api(path, data, method = 'POST') {
  const response = await fetch(path, data === undefined ? {} : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const result = await response.json();
  if (!response.ok) { const error = new Error(result.error || '요청 실패'); error.status = response.status; throw error; }
  return result;
}
function url(path, params) { return path + '?' + new URLSearchParams(params); }
function error(message) { $('ai-error').textContent = message; $('ai-error').hidden = !message; }
function richText(content) {
  const container = node('div', 'ai-message-text');
  const parts = content.split(/(```[^\n]*\n[\s\S]*?```)/g);
  for (const part of parts) {
    if (part.startsWith('```')) { container.append(node('pre', '', part.replace(/^```[^\n]*\n/, '').replace(/```$/, ''))); continue; }
    const paragraph = node('div'); let index = 0;
    const pattern = /\*\*([^*]+)\*\*|`([^`\n]+)`/g; let match;
    while ((match = pattern.exec(part))) {
      paragraph.append(document.createTextNode(part.slice(index, match.index)));
      paragraph.append(node(match[1] ? 'strong' : 'code', '', match[1] || match[2])); index = pattern.lastIndex;
    }
    paragraph.append(document.createTextNode(part.slice(index))); container.append(paragraph);
  }
  return container;
}
function proposalCard(proposal) {
  const card = node('details', 'ai-proposal'); card.open = proposal.status === 'pending';
  const title = node('summary'); title.append(node('span', '', proposal.path), node('span', 'ai-proposal-status', ({ pending: '수정안', applied: '적용됨', dismissed: '제외됨' })[proposal.status])); card.append(title);
  if (proposal.explanation) card.append(node('p', 'ai-proposal-reason', proposal.explanation));
  card.append(node('div', 'ai-diff-label', '원문'), node('pre', 'ai-diff-old', proposal.old_text), node('div', 'ai-diff-label', '수정안'), node('pre', 'ai-diff-new', proposal.new_text));
  if (proposal.status === 'pending') {
    const actions = node('div', 'ai-proposal-actions');
    const apply = node('button', 'button primary', state.applying === proposal.id ? '저장 중…' : '편집기에 적용');
    apply.disabled = state.busy || !!state.applying;
    apply.addEventListener('click', () => applyProposal(proposal));
    const dismiss = node('button', 'text-button', '제외'); dismiss.disabled = state.busy || !!state.applying;
    dismiss.addEventListener('click', () => updateProposal(proposal, 'dismissed').catch(exception => error(exception.message)));
    actions.append(apply, dismiss); card.append(actions);
  }
  return card;
}
function renderMessages() {
  const list = $('ai-messages');
  const stick = list.scrollHeight - list.scrollTop - list.clientHeight < 90;
  list.replaceChildren();
  if (!state.session?.messages?.length) {
    const empty = node('div', 'ai-empty'); empty.append(node('strong', '', '함께 더 명확하게 써보세요.'), node('p', '', '현재 파일과 선택한 문장을 바탕으로 교정을 제안합니다.'));
    const quick = node('div', 'ai-quick');
    for (const label of ['선택한 문장 교정', '가독성 점검']) {
      const button = node('button', '', label); button.addEventListener('click', () => { $('ai-input').value = label === '가독성 점검' ? '현재 파일에서 가독성을 떨어뜨리는 표현을 찾아 꼭 필요한 수정만 제안해줘.' : '선택한 문장을 의미와 LaTeX 명령을 유지하면서 더 자연스럽고 명확하게 교정해줘.'; $('ai-input').focus(); }); quick.append(button);
    }
    empty.append(quick); list.append(empty);
  }
  for (const message of state.session?.messages || []) {
    const article = node('article', 'ai-message ' + message.role + (message.status === 'error' ? ' failed' : ''));
    const heading = node('div', 'ai-message-heading', message.role === 'user' ? '나' : '✦ AI assistant');
    if (message.file) heading.append(node('span', 'ai-file-tag', message.file + (message.selection ? ' · 선택 영역' : '')));
    article.append(heading, richText(message.content));
    for (const proposal of message.proposals || []) article.append(proposalCard(proposal));
    if (message.events?.length) {
      const trace = node('details', 'ai-trace'); trace.append(node('summary', '', `도구 및 문맥 처리 ${message.events.length}건`));
      for (const event of message.events) trace.append(node('div', '', event.description)); article.append(trace);
    }
    list.append(article);
  }
  if (stick || state.busy) list.scrollTop = list.scrollHeight;
  updateBusy();
}
function updateBusy() {
  $('ai-send').textContent = state.busy ? '■' : '↑';
  $('ai-send').title = state.busy ? '응답 중지 요청' : '보내기';
  $('ai-send').setAttribute('aria-label', state.busy ? 'AI 응답 중지' : 'AI 메시지 보내기');
  $('ai-send').disabled = !state.project || !state.session || !!state.applying;
  $('ai-new').disabled = state.busy || !!state.applying;
  $('ai-progress').hidden = !state.busy;
  $('ai-stage').textContent = state.run?.stage || '응답 준비 중…';
  const run = state.run;
  $('ai-memory').textContent = run?.tokens ? `문맥 ${run.tokens.toLocaleString()}/${run.context_window.toLocaleString()} · 요약 ${run.compactions}회${run.context_truncated ? ' · 파일 일부 첨부' : ''}` : `현재 파일 자동 첨부${state.session?.compactions ? ` · 요약 ${state.session.compactions}회` : ''}`;
  updateLiveAnswer();
}
function updateLiveAnswer() {
  const list = $('ai-messages');
  let article = $('ai-live-answer');
  const text = state.busy && state.run?.partial_answer;
  if (!text) { article?.remove(); return; }
  if (article?.lastText === text) return;
  const stick = list.scrollHeight - list.scrollTop - list.clientHeight < 90;
  if (!article) {
    article = node('article', 'ai-message assistant ai-live-answer'); article.id = 'ai-live-answer'; list.append(article);
  }
  article.lastText = text;
  article.replaceChildren(node('div', 'ai-message-heading', '✦ AI assistant · 작성 중'), richText(text));
  if (stick) list.scrollTop = list.scrollHeight;
}
async function loadSession(id) {
  const epoch = state.epoch, project = state.project;
  let session;
  try { session = await api(url('/api/ai/session', { project, ...(id ? { session: id } : {}) })); }
  catch (exception) { if (exception.status === 404 && id) session = await api(url('/api/ai/session', { project })); else throw exception; }
  if (epoch !== state.epoch) return;
  state.session = session; localSet('session:' + project, session.id); renderMessages();
  if (session.active_run) { state.busy = true; state.run = { id: session.active_run, stage: '진행 중인 응답을 불러오는 중…' }; updateBusy(); followRun(session.active_run, epoch); }
}
let metadataFrame;
function editorChanged() {
  cancelAnimationFrame(metadataFrame);
  metadataFrame = requestAnimationFrame(async () => {
    const context = window.folioAssistant?.getContext();
    if (!context) return;
    $('ai-context').textContent = context.path ? context.path + (context.selection.text ? ` · ${context.selection.start_line}–${context.selection.end_line}행 선택` : '') : '파일을 선택하면 자동으로 문맥에 포함합니다';
    $('ai-context').title = context.path || '';
    if (context.project && context.project !== state.project) {
      state.feed?.close(); state.feed = null;
      if (state.project) localSet('draft:' + state.project, $('ai-input').value);
      state.project = context.project; state.epoch++; state.session = null; state.run = null; state.busy = false;
      $('ai-input').value = localGet('draft:' + state.project) || ''; error(''); renderMessages();
      try { await loadSession(localGet('session:' + state.project)); } catch (exception) { error(exception.message); }
    }
    updateBusy();
  });
}
window.addEventListener('folio:editor-context', editorChanged);
async function send() {
  if (state.busy) {
    if (state.run?.id) { try { await api('/api/ai/cancel', { id: state.run.id }); $('ai-stage').textContent = '중지 요청을 보냈습니다…'; } catch (exception) { error(exception.message); } }
    return;
  }
  const message = $('ai-input').value.trim(), context = window.folioAssistant?.getContext();
  if (!message || !context?.project || !state.session) return;
  const epoch = state.epoch; state.busy = true; error(''); updateBusy();
  try {
    const run = await api('/api/ai/chat', { project: state.project, session: state.session.id, message, context });
    if (epoch !== state.epoch) return;
    state.run = run; $('ai-input').value = ''; localSet('draft:' + state.project, '');
    state.session = await api(url('/api/ai/session', { project: state.project, session: state.session.id }));
    if (epoch !== state.epoch) return;
    renderMessages(); followRun(run.id, epoch);
  } catch (exception) { if (epoch === state.epoch) { state.busy = false; error(exception.message); updateBusy(); } }
}
async function finishRun(run, epoch) {
  const session = await api(url('/api/ai/session', { project: state.project, session: state.session.id }));
  if (epoch !== state.epoch) return;
  state.run = run; state.busy = false; state.session = session;
  renderMessages(); $('ai-messages').scrollTop = $('ai-messages').scrollHeight;
  if (run.error) error(run.error);
}
function followRun(id, epoch) {
  state.feed?.close(); state.feed = null;
  if (!('EventSource' in window)) { poll(id, epoch); return; }
  const source = new EventSource(url('/api/ai/stream', { id }));
  state.feed = source;
  source.addEventListener('update', event => {
    if (epoch !== state.epoch) { source.close(); return; }
    try {
      const run = JSON.parse(event.data);
      if (run.status !== 'running') {
        source.close(); if (state.feed === source) state.feed = null;
        finishRun(run, epoch).catch(exception => { if (epoch === state.epoch) { state.busy = false; error(exception.message); updateBusy(); } });
      } else { state.run = run; updateBusy(); }
    } catch (exception) { source.close(); if (epoch === state.epoch) poll(id, epoch); }
  });
  source.onerror = () => {
    source.close(); if (state.feed === source) state.feed = null;
    if (epoch === state.epoch) poll(id, epoch);
  };
}
async function poll(id, epoch) {
  while (epoch === state.epoch) {
    try {
      const run = await api(url('/api/ai/run', { id })); if (epoch !== state.epoch) return;
      if (run.status !== 'running') {
        await finishRun(run, epoch);
        return;
      }
      state.run = run; updateBusy();
    } catch (exception) { if (epoch === state.epoch) { state.busy = false; error(exception.message); updateBusy(); } return; }
    await new Promise(resolve => setTimeout(resolve, 700));
  }
}
async function updateProposal(proposal, status) {
  const epoch = state.epoch;
  await api('/api/ai/proposal', { project: state.project, session: state.session.id, proposal: proposal.id, status });
  const session = await api(url('/api/ai/session', { project: state.project, session: state.session.id }));
  if (epoch === state.epoch) { state.session = session; renderMessages(); }
}
async function applyProposal(proposal) {
  state.applying = proposal.id; error(''); renderMessages();
  try { await window.folioAssistant.applyEdit(proposal); await updateProposal(proposal, 'applied'); }
  catch (exception) { error(exception.message); }
  finally { state.applying = null; renderMessages(); }
}
$('ai-send').addEventListener('click', send);
$('ai-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!state.busy && !state.applying) send(); } });
$('ai-input').addEventListener('input', () => { if (state.project) localSet('draft:' + state.project, $('ai-input').value); });
$('ai-new').addEventListener('click', () => { if (!state.busy) { state.run = null; error(''); loadSession(null).catch(exception => error(exception.message)); } });
function setCollapsed(collapsed) { root.classList.toggle('ai-collapsed', collapsed); $('ai-resizer').hidden = collapsed; localSet('collapsed', collapsed ? '1' : '0'); }
$('ai-toggle').addEventListener('click', () => setCollapsed(!root.classList.contains('ai-collapsed')));
setCollapsed(localGet('collapsed') === '1');
function updateSettings(result) {
  state.settings = result; const config = result.config;
  const model = result.active_model || config.model;
  $('ai-model').textContent = (config.provider === 'exaone' ? 'EXAONE · ' : config.provider === 'openai' ? 'OpenAI · ' : '') + model.split('/').pop(); $('ai-model').title = `${model} · ${result.active_base_url || 'localhost:' + config.port}${result.connected ? ' · 연결됨' : ' · 연결 안 됨'}`;
  $('ai-model').classList.toggle('disconnected', !result.connected);
  $('ai-port').value = config.port; $('ai-model-input').value = config.model; $('ai-window').value = config.context_window;
  $('ai-output').value = config.max_output_tokens; $('ai-threshold').value = Math.round(config.compaction_threshold * 100);
  $('ai-stream').checked = config.stream;
  $('ai-thinking').checked = config.chat_template_kwargs.enable_thinking === true;
  $('ai-thinking-level').value = config.thinking_level;
  $('ai-thinking-budget').value = config.thinking_token_budget;
  $('ai-provider').value = config.provider || 'local';
  $('ai-provider').disabled = !result.active_model;
  $('ai-provider').title = result.active_model ? '' : '진행 중인 요청을 마친 뒤 제공자 전환 기능을 반영합니다.';
  $('ai-api-key').value = ''; $('ai-clear-key').checked = false;
  providerDrafts = {};
  renderProviderFields(config.provider || 'local', model, config.provider === 'exaone' ? config.exaone_reasoning_effort : config.provider === 'openai' ? config.openai_reasoning_effort : config.thinking_level);
  $('ai-prompt-path').textContent = result.prompt_path; $('ai-config-path').textContent = result.config_path;
  $('ai-connection').textContent = result.connected ? `연결됨 · 서버 최대 ${result.server_context_window.toLocaleString()} 토큰 · 적용 ${result.context_window.toLocaleString()} 토큰` : result.error;
}
function updateThinkingControls() {
  const exaone = $('ai-provider').value === 'exaone';
  $('ai-thinking').disabled = exaone;
  if (exaone) $('ai-thinking').checked = true;
  const enabled = $('ai-thinking').checked, level = $('ai-thinking-level').value;
  const total = Number($('ai-output').value) || 0, available = Math.max(0, total - 128);
  $('ai-thinking-level').disabled = !enabled;
  $('ai-output').setCustomValidity(enabled && total < 256 ? 'Thinking을 사용하려면 전체 출력 한도를 256 토큰 이상으로 설정해 주세요.' : '');
  if (exaone || $('ai-provider').value === 'openai') {
    $('ai-thinking-custom-field').hidden = true; $('ai-thinking-budget').disabled = true; $('ai-thinking-budget').required = false;
    $('ai-thinking-help').classList.remove('form-error');
    $('ai-thinking-help').textContent = exaone ? `K-EXAONE 2.5는 항상 추론합니다. 추론 강도: ${level}. 추론과 최종 답변 모두 출력 한도에 포함됩니다.` : enabled ? `OpenAI 추론 강도: ${level}. 추론과 답변 토큰은 전체 출력 한도에 포함됩니다. 토큰 수는 모델이 결정합니다.` : 'Thinking을 끄면 OpenAI에는 reasoning.effort=none을 전달합니다.';
    return;
  }
  const budgets = { low: Math.min(256, Math.floor(total / 4), available), medium: Math.min(1024, Math.floor(total / 2), available), high: Math.min(4096, Math.floor(total * 3 / 4), available) };
  const labels = { low: '낮음', medium: '보통', high: '높음' };
  for (const option of $('ai-thinking-level').options) if (option.value !== 'custom') option.textContent = `${labels[option.value]} · 최대 ${budgets[option.value].toLocaleString()} 토큰`;
  $('ai-thinking-level').disabled = !enabled;
  $('ai-thinking-custom-field').hidden = level !== 'custom';
  $('ai-thinking-budget').disabled = !enabled || level !== 'custom';
  $('ai-thinking-budget').required = enabled && level === 'custom';
  $('ai-thinking-budget').max = Math.max(1, available);
  $('ai-output').setCustomValidity(enabled && total < 256 ? 'Thinking을 사용하려면 전체 출력 한도를 256 토큰 이상으로 설정해 주세요.' : '');
  const budget = level === 'custom' ? Number($('ai-thinking-budget').value) : budgets[level];
  const invalidBudget = enabled && level === 'custom' && budget > available;
  $('ai-thinking-help').classList.toggle('form-error', invalidBudget);
  $('ai-thinking-help').textContent = invalidBudget ? `현재 전체 출력 한도에서는 추론 상한을 ${available.toLocaleString()} 토큰 이하로 지정해 주세요. 더 많은 추론이 필요하면 전체 출력 한도를 늘릴 수 있습니다.` : enabled ? `현재 추론 상한 ${Math.max(0, budget || 0).toLocaleString()} 토큰 · 전체 출력 한도에 포함됩니다. 모델은 상한보다 일찍 답할 수 있습니다. 최종 답변에 최소 128 토큰을 남깁니다.` : 'Thinking을 켜면 추론 정도와 토큰 상한을 조절할 수 있습니다.';
}
for (const id of ['ai-thinking', 'ai-thinking-level', 'ai-thinking-budget', 'ai-output']) $(id).addEventListener('input', updateThinkingControls);
$('ai-settings').addEventListener('click', async () => {
  $('ai-settings-error').textContent = ''; dialog.showModal();
  for (const field of $('ai-settings-form').elements) field.disabled = true;
  try { updateSettings(await api('/api/ai/config')); } catch (exception) { $('ai-settings-error').textContent = exception.message; }
  finally {
    for (const field of $('ai-settings-form').elements) field.disabled = false;
    $('ai-provider').disabled = !state.settings?.active_model;
    const cloud = $('ai-provider').value !== 'local';
    $('ai-port').disabled = cloud; for (const field of openaiFields.querySelectorAll('input')) field.disabled = !cloud;
    updateThinkingControls();
  }
});
$('ai-settings-close').addEventListener('click', () => dialog.close());
dialog.addEventListener('close', () => { $('ai-api-key').value = ''; $('ai-clear-key').checked = false; });
$('ai-settings-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const provider = $('ai-provider').value, cloud = provider !== 'local', openai = provider === 'openai', exaone = provider === 'exaone';
    const result = await api('/api/ai/config', { provider, port: Number($('ai-port').value), model: $('ai-model-input').value.trim(),
      ...(openai ? { openai_base_url: $('ai-base-url').value.trim(), openai_reasoning_effort: $('ai-thinking-level').value } : {}),
      ...(exaone ? { exaone_reasoning_effort: $('ai-thinking-level').value } : {}),
      context_window: Number($('ai-window').value), max_output_tokens: Number($('ai-output').value), compaction_threshold: Number($('ai-threshold').value) / 100, stream: $('ai-stream').checked,
      ...(!exaone ? { enable_thinking: $('ai-thinking').checked } : {}),
      ...(!cloud ? { thinking_level: $('ai-thinking-level').value } : {}),
      thinking_token_budget: Number($('ai-thinking-budget').value || state.settings?.config.thinking_token_budget || 1024),
      ...(cloud && $('ai-api-key').value ? { api_key: $('ai-api-key').value } : {}), ...(cloud && $('ai-clear-key').checked ? { clear_api_key: true } : {}) }, 'PUT');
    updateSettings(result); $('ai-settings-error').textContent = result.connected ? '' : result.error;
    if (result.connected) dialog.close();
  } catch (exception) { $('ai-settings-error').textContent = exception.message; }
  finally { button.disabled = false; }
});
let dragging = false;
const resizer = $('ai-resizer');
resizer.addEventListener('pointerdown', event => { dragging = true; resizer.setPointerCapture(event.pointerId); document.body.style.userSelect = 'none'; });
resizer.addEventListener('pointermove', event => {
  if (!dragging) return;
  const height = Math.min($('editor-panel').clientHeight - 180, Math.max(190, root.getBoundingClientRect().bottom - event.clientY));
  root.style.setProperty('--ai-height', height + 'px'); localSet('height', String(height));
});
function stopDrag() { dragging = false; document.body.style.userSelect = ''; }
resizer.addEventListener('pointerup', stopDrag); resizer.addEventListener('lostpointercapture', stopDrag);
resizer.addEventListener('keydown', event => {
  if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return; event.preventDefault();
  const height = Math.min($('editor-panel').clientHeight - 180, Math.max(190, root.clientHeight + (event.key === 'ArrowUp' ? 20 : -20)));
  root.style.setProperty('--ai-height', height + 'px'); localSet('height', String(height));
});
const height = Number(localGet('height')); if (height >= 190) root.style.setProperty('--ai-height', height + 'px');
api('/api/ai/config').then(updateSettings).catch(exception => { $('ai-model').textContent = '설정 필요'; $('ai-model').title = exception.message; });
renderMessages(); editorChanged();

const $ = id => document.getElementById(id);
const element = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('ko', { granularity: 'sentence' }) : null;

export function sentenceRange(text, at) {
  at = Math.max(0, Math.min(text.length, at));
  let start = 0, end = text.length;
  for (const match of text.matchAll(/\n\s*\n/g)) {
    if (match.index + match[0].length <= at) start = match.index + match[0].length;
    else { end = match.index; break; }
  }
  const paragraph = text.slice(start, end);
  const sentences = segmenter ? [...segmenter.segment(paragraph)] : [...paragraph.matchAll(/[^.!?。！？]+[.!?。！？]*(?:\s+|$)/g)].map(item => ({index:item.index, segment:item[0]}));
  for (const item of sentences) {
    if (at < start + item.index + item.segment.length || item === sentences.at(-1)) {
      const leading = item.segment.length - item.segment.trimStart().length;
      return { start: start + item.index + leading, end: start + item.index + item.segment.trimEnd().length };
    }
  }
  return { start, end };
}

export class SentenceComments {
  constructor({editor, current, save, open, toast}) {
    Object.assign(this, {editor, current, save, open, toast});
    this.comments = []; this.marks = []; this.sequence = 0;
    const toolbar = document.querySelector('.editor-toolbar');
    const add = element('button', 'text-button annotation-add', '코멘트 +'); add.id = 'add-comment'; add.title = '선택한 문장에 코멘트 · Ctrl/⌘ + Alt + M';
    const toggle = element('button', 'text-button annotation-toggle'); toggle.id = 'toggle-comments'; toggle.setAttribute('aria-controls','comments-panel');toggle.setAttribute('aria-expanded','false');
    toggle.append(element('span','','코멘트'),element('span','comment-count','0')); toolbar.insertBefore(add,$('editor-find'));toolbar.insertBefore(toggle,$('editor-find'));
    this.panel = element('section','comments-panel');this.panel.id='comments-panel';this.panel.hidden=true;this.panel.setAttribute('aria-label','문장 코멘트 목록');
    this.panel.innerHTML='<div class="comments-heading"><strong>문장 코멘트</strong><button id="comments-refresh" class="text-button">새로고침</button><button id="comments-close" class="icon-button" aria-label="코멘트 닫기">×</button></div><div class="comments-filter"><label><input id="comments-current" type="checkbox" checked> 현재 파일만</label><label><input id="comments-resolved" type="checkbox"> 해결된 항목</label></div><p class="comments-help">문장을 선택한 뒤 ‘코멘트 +’를 누르세요. 인용문을 누르면 원문으로 이동합니다.</p><p id="comments-error" class="form-error" role="alert"></p><div id="comments-list" class="comments-list"></div>';
    $('workspace').insertBefore(this.panel,$('editor-panel'));
    this.dialog=element('dialog','comment-dialog');this.dialog.id='comment-dialog';
    this.dialog.innerHTML='<form id="comment-form"><div class="dialog-heading"><h2 id="comment-title">문장에 코멘트 남기기</h2><button id="comment-cancel" type="button" class="icon-button" aria-label="코멘트 작성 취소">×</button></div><p id="comment-file" class="field-help"></p><blockquote id="comment-quote" class="comment-quote"></blockquote><label class="field" for="comment-body">코멘트<textarea id="comment-body" rows="5" maxlength="6000" required placeholder="이 문장에 대한 의견이나 수정 제안을 적어주세요."></textarea></label><p id="comment-error" class="form-error" role="alert"></p><button id="comment-submit" class="button primary full-width">코멘트 저장</button></form>';
    document.body.append(this.dialog);
    add.addEventListener('click',()=>this.compose().catch(error=>toast(error.message,true)));
    toggle.addEventListener('click',()=>{this.setOpen(this.panel.hidden);if(!this.panel.hidden)this.refresh();});
    $('comments-close').addEventListener('click',()=>this.setOpen(false));$('comments-refresh').addEventListener('click',()=>this.refresh());
    for(const id of ['comments-current','comments-resolved'])$(id).addEventListener('change',()=>this.render());
    $('comment-cancel').addEventListener('click',()=>this.dialog.close());
    this.dialog.addEventListener('close',()=>{this.target=null;});
    $('comment-form').addEventListener('submit',event=>{event.preventDefault();this.submit();});
    editor.setOption('extraKeys',{...editor.getOption('extraKeys'),'Ctrl-Alt-M':()=>this.compose().catch(error=>toast(error.message,true)),'Cmd-Alt-M':()=>this.compose().catch(error=>toast(error.message,true))});
    this.changedTimer=null;
    editor.on('change',()=>{clearTimeout(this.changedTimer);this.changedTimer=setTimeout(()=>this.renderMarks(),250);});
    editor.getWrapperElement().addEventListener('click',event=>{
      const anchor=event.target.closest('[data-comment-id]');if(!anchor)return;
      clearTimeout(this.anchorClickTimer);
      if(event.detail>1)return;
      this.anchorClickTimer=setTimeout(()=>{
        this.setOpen(true);
        this.panel.querySelector(`[data-id="${anchor.dataset.commentId}"]`)?.scrollIntoView({block:'nearest'});
      },280);
    });
    editor.getWrapperElement().addEventListener('dblclick',()=>clearTimeout(this.anchorClickTimer));
  }
  async request(data, method='POST') {
    const response=await fetch('/api/comments',{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'코멘트를 저장하지 못했습니다.');return result;
  }
  setOpen(open, remember=true) {
    this.panel.hidden=!open;
    $('workspace').classList.toggle('comments-visible',open);
    $('toggle-comments').setAttribute('aria-expanded',String(open));
    $('toggle-comments').title=open?'문장 코멘트 숨기기':'문장 코멘트 표시';
    if(remember)try{localStorage.setItem('folio:comments-visible',String(open));}catch{}
  }
  async refresh() {
    const sequence=++this.sequence, context=this.current();
    if(!context.project)return;
    try {
      const response=await fetch('/api/comments?'+new URLSearchParams({project:context.project}));const result=await response.json();
      if(!response.ok)throw new Error(result.error||'코멘트를 불러오지 못했습니다.');
      if(sequence!==this.sequence||context.project!==this.current().project)return;
      this.comments=result.comments;$('comments-error').textContent='';this.render();
    }catch(error){if(sequence===this.sequence){$('comments-error').textContent=error.message;if(!this.panel.hidden)this.toast(error.message,true);}}
  }
  reset() {
    ++this.sequence;this.comments=[];this.clearMarks();
    let open=false;try{open=localStorage.getItem('folio:comments-visible')==='true';}catch{}
    this.setOpen(open,false);this.render();
  }
  selectedRange() {
    const doc=this.editor.getDoc();
    if(this.editor.somethingSelected())return {from:this.editor.getCursor('from'),to:this.editor.getCursor('to')};
    const range=sentenceRange(doc.getValue(),doc.indexFromPos(this.editor.getCursor()));
    return {from:doc.posFromIndex(range.start),to:doc.posFromIndex(range.end)};
  }
  async compose(comment=null) {
    if(this.dialog.open)return;
    const context=this.current();
    if(!context.doc)throw new Error('텍스트 파일에서 코멘트를 달 문장을 선택해 주세요.');
    let target;
    if(comment)target={project:context.project,id:comment.id,revision:comment.revision,quote:comment.quote,path:comment.path,line:comment.line};
    else {
      const range=this.selectedRange(),quote=this.editor.getRange(range.from,range.to);
      if(!quote.trim()||quote.length>12000)throw new Error('코멘트를 달 문장을 12,000자 이내로 선택해 주세요.');
      // Live CodeMirror marks keep the range aligned if edits happen while saving.
      const mark=context.doc.cm.markText(range.from,range.to);
      try {
        await this.save(context.doc);
        if(this.current().project!==context.project||this.current().path!==context.path)throw new Error('파일을 전환했습니다. 문장을 다시 선택해 주세요.');
        const anchor=mark.find();if(!anchor)throw new Error('원문이 바뀌었습니다. 문장을 다시 선택해 주세요.');
        const selected=context.doc.cm.getRange(anchor.from,anchor.to);
        target={project:context.project,path:context.path,source_revision:context.doc.revision,from:anchor.from,to:anchor.to,quote:selected,line:anchor.from.line+1};
      }finally{mark.clear();}
    }
    this.target=target;$('comment-title').textContent=comment?'코멘트 수정':'문장에 코멘트 남기기';
    $('comment-file').textContent=target.path+' · '+target.line+'행';$('comment-quote').textContent=target.quote;
    $('comment-body').value=comment?.text||'';$('comment-error').textContent='';this.dialog.showModal();$('comment-body').focus();
  }
  async submit() {
    const target=this.target;if(!target)return;
    $('comment-submit').disabled=true;
    try {
      const result=await this.request({...target,text:$('comment-body').value},target.id?'PUT':'POST');
      if(this.current().project===target.project){this.comments=result.comments;this.render();this.setOpen(true);}
      this.dialog.close();this.toast('코멘트를 저장했습니다.');
    }catch(error){$('comment-error').textContent=error.message;}finally{$('comment-submit').disabled=false;}
  }
  async update(comment, changes, method='PUT') {
    const project=this.current().project;
    try {
      const result=await this.request({project,id:comment.id,revision:comment.revision,...changes},method);
      if(project===this.current().project){this.comments=result.comments;this.render();}
    }catch(error){this.toast(error.message,true);this.refresh();}
  }
  render() {
    const current=this.current();
    $('add-comment').disabled=!current.doc;
    $('toggle-comments').querySelector('.comment-count').textContent=this.comments.filter(item=>!item.resolved).length;
    const items=this.comments.filter(item=>(!$('comments-current').checked||item.path===current.path)&&($('comments-resolved').checked||!item.resolved));
    const list=$('comments-list');list.replaceChildren();
    for(const item of items) {
      const card=element('article','comment-card'+(item.resolved?' resolved':''));card.dataset.id=item.id;
      const heading=element('div','comment-card-heading'),path=element('span','',item.path+' · '+(item.anchor?item.anchor.from.line+1:item.line)+'행');
      heading.append(path,element('span','comment-state',item.resolved?'해결됨':!item.anchor?'원문 위치 확인 필요':''));
      const quote=element('button','comment-source',item.quote);quote.title='원문 위치로 이동';
      quote.addEventListener('click',async()=>{
        if(!item.anchor){this.toast('원문이 수정되거나 삭제되었습니다. 인용문을 기준으로 내용을 확인해 주세요.',true);return;}
        try {
          await this.open(item.path,item.anchor.from.line+1);
          if(this.current().project!==current.project||this.current().path!==item.path)return;
          const doc=this.editor.getDoc(),start=doc.indexFromPos(item.anchor.from),end=doc.indexFromPos(item.anchor.to);
          if(doc.getValue().slice(start,end)!==item.quote){this.toast('원문이 변경되었습니다. 저장 후 코멘트 목록을 새로고침해 주세요.',true);return;}
          this.editor.setSelection(item.anchor.from,item.anchor.to);this.editor.scrollIntoView({from:item.anchor.from,to:item.anchor.to},70);this.editor.focus();
          if(window.matchMedia('(max-width:640px)').matches)this.setOpen(false);
        }catch(error){this.toast(error.message,true);}
      });
      const body=element('p','comment-body',item.text),meta=element('div','comment-date',new Date(item.updated_at*1000).toLocaleString('ko-KR'));
      const actions=element('div','comment-actions');
      for(const [label,handler] of [['수정',()=>this.compose(item).catch(error=>this.toast(error.message,true))],[item.resolved?'다시 열기':'해결',()=>this.update(item,{resolved:!item.resolved})],['삭제',()=>this.update(item,{},'DELETE')]]){
        const button=element('button','text-button',label);button.addEventListener('click',handler);actions.append(button);
      }
      card.append(heading,quote,body,meta,actions);list.append(card);
    }
    if(!items.length)list.append(element('p','comments-empty','표시할 코멘트가 없습니다. 문장을 선택하고 ‘코멘트 +’를 눌러보세요.'));
    this.renderMarks();
  }
  clearMarks() {for(const mark of this.marks)mark.clear();this.marks=[];}
  renderMarks() {
    this.clearMarks();const current=this.current();if(!current.doc)return;
    for(const item of this.comments.filter(item=>item.path===current.path&&!item.resolved&&item.anchor)) {
      if(current.doc.cm.getRange(item.anchor.from,item.anchor.to)!==item.quote)continue;
      this.marks.push(current.doc.cm.markText(item.anchor.from,item.anchor.to,{className:'comment-anchor',title:'코멘트: '+item.text,attributes:{'data-comment-id':item.id}}));
    }
  }
}

const CITATIONS = new Set(['cite','citep','citet','citealp','citealt','citeauthor','citeyear','citeyearpar','autocite','parencite','textcite','footcite','smartcite','supercite','nocite','Cite','Citep','Citet','Autocite','Parencite','Textcite','Footcite']);
const REFERENCES = new Set(['ref','eqref','pageref','autoref','cref','Cref','vref','Vref','nameref','subref','cpageref','Cpageref']);
const node = (tag, className, text) => { const item=document.createElement(tag);if(className)item.className=className;if(text!==undefined)item.textContent=text;return item; };
const normalize = value => value.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase();

export function completionContext(text, index) {
  const start=Math.max(0,index-8192), before=text.slice(start,index);
  // Ignore comments without changing positions (escaped percent signs remain literal).
  const clean=before.replace(/(?:^|\n)[^\n]*/g,line=>{
    for(let i=0;i<line.length;i++)if(line[i]==='%'){
      let slash=i;while(slash>0&&line[slash-1]==='\\')slash--;
      if((i-slash)%2===0)return line.slice(0,i)+' '.repeat(line.length-i);
    }
    return line;
  });
  const match=/\\([A-Za-z]+)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]*)$/.exec(clean);
  if(!match)return null;
  let slash=match.index;while(slash>0&&clean[slash-1]==='\\')slash--;
  if((match.index-slash)%2)return null;
  const kind=CITATIONS.has(match[1])?'citation':REFERENCES.has(match[1])?'label':null;
  if(!kind)return null;
  const bodyStart=start+match.index+match[0].length-match[2].length;
  const segmentStart=bodyStart+match[2].lastIndexOf(',')+1;
  const prefix=text.slice(segmentStart,index),leading=/^\s*/.exec(prefix)[0].length;
  const from=Math.min(index,segmentStart+leading), to=index+/^[^\s,{}\\]*/.exec(text.slice(index))[0].length;
  const tail=text.slice(bodyStart), delimiter=tail.search(/[{}]/),closed=delimiter>=0&&tail[delimiter]==='}';
  const bodyEnd=closed?bodyStart+delimiter:Math.max(index,to);
  const segments=text.slice(bodyStart,bodyEnd).split(',');
  const activeSegment=match[2].split(',').length-1;
  const used=segments.filter((_,i)=>i!==activeSegment).map(value=>value.trim()).filter(Boolean);
  return {kind,command:match[1],query:text.slice(from,index).trim(),from,to,closed,used};
}

export class LatexCompletion {
  constructor({editor,current,buffers,toast}) {
    Object.assign(this,{editor,current,buffers,toast});this.items=[];this.rows=[];this.project=null;this.dirty=true;this.requestId=0;this.selected=0;this.composing=false;
    this.popup=node('div','latex-completion');this.popup.id='latex-completion';this.popup.hidden=true;
    this.popup.setAttribute('aria-label','LaTeX 자동완성');
    this.heading=node('div','completion-heading');this.list=node('div','completion-list');this.list.setAttribute('role','listbox');this.list.id='latex-completion-list';
    this.footer=node('div','completion-footer','↑↓ 선택 · Enter/Tab 삽입 · Esc 닫기 · Ctrl+Space 새로고침');
    this.popup.append(this.heading,this.list,this.footer);document.body.append(this.popup);
    this.keymap={Up:()=>this.move(-1),Down:()=>this.move(1),PageUp:()=>this.move(-6),PageDown:()=>this.move(6),Enter:()=>this.accept(),Tab:()=>this.accept(),Esc:()=>{this.close(true);}};
    editor.addKeyMap({'Ctrl-Space':()=>this.trigger(true)});
    editor.on('change',(_cm,change)=>{
      if(this.applying)return;
      if(this.popup.hidden)this.dirty=true;
      this.suppressed=null;clearTimeout(this.timer);
      // Let CodeMirror finish its selection update and IME composition first.
      this.timer=setTimeout(()=>this.trigger(false),90);
    });
    editor.on('cursorActivity',()=>{
      clearTimeout(this.cursorTimer);this.cursorTimer=setTimeout(()=>this.trigger(false),30);
    });
    editor.on('swapDoc',()=>{this.close();this.dirty=true;});
    editor.on('scroll',()=>this.position());
    editor.on('blur',()=>{this.blurTimer=setTimeout(()=>this.close(),130);});
    const field=editor.getInputField();
    field.addEventListener('compositionstart',()=>{this.composing=true;this.close();});
    field.addEventListener('compositionend',()=>{this.composing=false;setTimeout(()=>this.trigger(false),0);});
    window.addEventListener('resize',()=>this.position());
    document.addEventListener('pointerdown',event=>{if(!this.popup.contains(event.target)&&!editor.getWrapperElement().contains(event.target))this.close();});
  }
  context() {
    const current=this.current();
    if(!current.project||!current.path?.endsWith('.tex')||!this.editor.hasFocus()||this.editor.somethingSelected()||this.composing)return null;
    const cursor=this.editor.getCursor();
    if(this.editor.getTokenAt(cursor).type?.includes('comment'))return null;
    const result=completionContext(this.editor.getValue(),this.editor.indexFromPos(cursor));
    if(result)result.identity=current.project+':'+current.path+':'+result.from;
    return result;
  }
  async trigger(force=false) {
    const context=this.context();
    if(!context){this.close();return;}
    if(!force&&this.suppressed===context.identity)return;
    const current=this.current();
    if(force||this.project!==current.project||this.dirty||!this.loadedAt||Date.now()-this.loadedAt>15000){
      if(this.loading&&!force&&this.loadingProject===current.project){this.show(context,'후보를 읽는 중…');return;}
      const request=++this.requestId;this.loading=true;this.loadingProject=current.project;
      this.show(context,'프로젝트의 정의를 읽는 중…');
      try {
        const response=await fetch('/api/completions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:current.project,buffers:this.buffers()})});
        const result=await response.json();
        if(!response.ok)throw new Error(result.error||'자동완성 후보를 읽지 못했습니다.');
        if(request!==this.requestId||this.current().project!==current.project)return;
        this.items=result.items;this.project=current.project;this.dirty=false;this.loadedAt=Date.now();this.warnings=result.warnings||[];
      }catch(error){
        if(request===this.requestId){this.close();if(force)this.toast(error.message,true);}
        return;
      }finally{if(request===this.requestId)this.loading=false;}
    }
    const latest=this.context();if(!latest){this.close();return;}
    if(!force&&this.suppressed===latest.identity)return;
    const parts=normalize(latest.query).split(/\s+/).filter(Boolean);
    this.rows=this.items.filter(item=>item.kind===latest.kind&&!latest.used.includes(item.key)).map(item=>{
      const key=normalize(item.key),haystack=normalize([item.key,item.title,item.author,item.year].join(' '));
      const matches=parts.every(part=>haystack.includes(part));
      return {item,matches,rank:key===normalize(latest.query)?0:key.startsWith(normalize(latest.query))?1:2};
    }).filter(row=>row.matches).sort((a,b)=>a.rank-b.rank||a.item.key.localeCompare(b.item.key)).slice(0,80).map(row=>row.item);
    this.selected=0;this.show(latest);this.renderRows();
  }
  show(context,status='') {
    this.active=context;
    this.heading.textContent=(context.kind==='citation'?'참고문헌':'문서 참조')+' · \\'+context.command;
    if(this.popup.hidden){this.popup.hidden=false;this.editor.addKeyMap(this.keymap);}
    if(status){this.rows=[];this.list.replaceChildren(node('div','completion-empty',status));}
    const input=this.editor.getInputField();input.setAttribute('aria-controls',this.list.id);input.setAttribute('aria-expanded','true');
    this.position();
  }
  renderRows() {
    this.list.replaceChildren();
    for(const [index,item] of this.rows.entries()){
      const row=node('div','completion-option');row.id='completion-option-'+index;row.setAttribute('role','option');row.setAttribute('aria-selected',String(index===this.selected));
      row.append(node('strong','completion-key',item.key));
      if(item.title)row.append(node('span','completion-title',item.title));
      const detail=[item.author,item.year,item.path+':'+item.line].filter(Boolean).join(' · ');
      row.append(node('small','completion-detail',detail));
      row.addEventListener('pointerdown',event=>{event.preventDefault();clearTimeout(this.blurTimer);this.selected=index;this.accept();});
      row.addEventListener('pointermove',()=>{if(this.selected!==index){this.selected=index;this.renderSelection();}});
      this.list.append(row);
    }
    if(!this.rows.length)this.list.append(node('div','completion-empty',this.active.kind==='citation'?'일치하는 참고문헌이 없습니다. .bib 또는 \\bibitem 정의를 확인하세요.':'일치하는 참조가 없습니다. \\label{...} 정의를 확인하세요.'));
    this.footer.textContent=this.warnings.length?'일부 파일 제외 · Ctrl+Space 새로고침 · ↑↓ 선택 · Enter/Tab 삽입':'↑↓ 선택 · Enter/Tab 삽입 · Esc 닫기 · Ctrl+Space 새로고침';
    this.renderSelection();this.position();
  }
  renderSelection() {
    for(const [index,row] of [...this.list.children].entries()){
      const selected=index===this.selected&&this.rows.length>0;row.classList.toggle('active',selected);row.setAttribute('aria-selected',String(selected));
    }
    const row=this.list.children[this.selected];
    if(row&&this.rows.length){row.scrollIntoView({block:'nearest'});this.editor.getInputField().setAttribute('aria-activedescendant',row.id);}
  }
  move(delta) {
    if(!this.rows.length)return CodeMirror.Pass;
    this.selected=((this.selected+delta)%this.rows.length+this.rows.length)%this.rows.length;this.renderSelection();
  }
  accept() {
    const context=this.context(),item=this.rows[this.selected];
    if(!context||!item||context.identity!==this.active?.identity){this.close();return CodeMirror.Pass;}
    const from=this.editor.posFromIndex(context.from),to=this.editor.posFromIndex(context.to);
    this.applying=true;
    try {
      this.editor.replaceRange(item.key+(context.closed?'':'}'),from,to,'+complete');
      this.editor.setCursor(this.editor.posFromIndex(context.from+item.key.length));
      this.editor.focus();
    }finally{this.applying=false;this.close(true);}
  }
  position() {
    if(this.popup.hidden)return;
    const coords=this.editor.cursorCoords(null,'window'),width=Math.min(460,window.innerWidth-20);
    const left=Math.max(10,Math.min(coords.left,window.innerWidth-width-10));
    this.popup.style.width=width+'px';this.popup.style.left=left+'px';
    this.list.style.maxHeight=Math.max(90,Math.min(260,window.innerHeight*.4))+'px';
    const height=this.popup.offsetHeight;
    this.popup.style.top=(coords.bottom+height+10<=window.innerHeight?coords.bottom+5:Math.max(8,coords.top-height-5))+'px';
  }
  close(suppress=false) {
    if(suppress)this.suppressed=this.active?.identity;
    this.popup.hidden=true;this.editor.removeKeyMap(this.keymap);
    const input=this.editor.getInputField();input.setAttribute('aria-expanded','false');input.removeAttribute('aria-controls');input.removeAttribute('aria-activedescendant');
  }
  invalidate(){this.dirty=true;this.close();}
}

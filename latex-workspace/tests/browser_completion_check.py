"""Citation/ref completion in CodeMirror, using synthetic local documents only."""
import importlib.util
import os
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright,expect

spec=importlib.util.spec_from_file_location('completion_ai_tests',Path(__file__).with_name('test_ai_assistant.py'))
tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)
fixture=tests.AssistantTests();fixture.setUp()
(fixture.project/'references.bib').write_text('@article{smith2024, title={Reliable Agents}, author={Smith, Jane}, year={2024}}\n@misc{lee2025, title={Context Methods}, author={Lee, Mina}, year={2025}}\n@misc{alpha2023, title={Another Study}, year={2023}}')
(fixture.project/'labels.tex').write_text('\\section{Introduction}\n\\label{sec:intro}\n\\begin{equation}x=1\\label{eq:main}\\end{equation}')
server=tests.server
app=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);app.workspace=fixture.workspace
thread=threading.Thread(target=app.serve_forever,daemon=True);thread.start()
try:
 with sync_playwright() as p:
  options={'headless':True}
  if os.environ.get('FOLIO_BROWSER'):options['executable_path']=os.environ['FOLIO_BROWSER']
  browser=p.chromium.launch(**options)
  page=browser.new_page(viewport={'width':1500,'height':1020})
  errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
  requests=[];page.on('request',lambda request: requests.append(request.post_data_json) if request.url.endswith('/api/completions') else None)
  page.goto(f'http://127.0.0.1:{app.server_port}')
  expect(page.locator('#ai-send')).to_be_enabled(timeout=15000)
  page.locator('button.tree-item[data-path="section.tex"]').click()
  expect(page.locator('#breadcrumb')).to_have_text('section.tex')
  def set_text(text,at=None):
   page.evaluate('([text,at])=>{const cm=document.querySelector(".CodeMirror").CodeMirror;cm.setValue(text);cm.setCursor(cm.posFromIndex(at===null?text.length:at));cm.focus()}',[text,at])
  def value():return page.evaluate('document.querySelector(".CodeMirror").CodeMirror.getValue()')
  set_text('')
  page.keyboard.type('\\cite{Sm',delay=60)
  expect(page.locator('#latex-completion')).to_be_visible()
  expect(page.locator('.completion-option')).to_have_count(1)
  expect(page.locator('.completion-title')).to_have_text('Reliable Agents')
  expect(page.locator('.completion-detail')).to_contain_text('Smith, Jane · 2024')
  page.keyboard.press('Enter')
  assert value()=='\\cite{smith2024}',repr(value())
  expect(page.locator('#latex-completion')).not_to_be_visible()
  page.keyboard.press('Control+z')
  assert 'smith2024' not in value()
  print('PASS: automatic cite popup, title/author/year metadata, Enter insertion and undo')
  text='\\cite{smith2024, le}'
  set_text(text,len(text)-1)
  expect(page.locator('.completion-key')).to_have_text('lee2025')
  page.keyboard.press('Tab')
  assert value()=='\\cite{smith2024, lee2025}',repr(value())
  text='\\cite{smith2024, lee2025}'
  set_text(text,text.index('smith')+2)
  expect(page.locator('.completion-key')).to_have_text('smith2024')
  page.keyboard.press('Enter')
  assert value()==text,'Mid-key completion must preserve the next citation'
  text='\\citep[see][p.~2]{Context}'
  set_text(text,len(text)-1)
  expect(page.locator('.completion-key')).to_have_text('lee2025')
  page.locator('.completion-option').click()
  assert value()=='\\citep[see][p.~2]{lee2025}'
  print('PASS: multiple citations, mid-key replacement, natbib optional notes, title search and mouse selection')
  text='See \\ref{sec:in}'
  set_text(text,len(text)-1)
  expect(page.locator('.completion-key')).to_have_text('sec:intro')
  expect(page.locator('.completion-title')).to_have_text('Introduction')
  page.keyboard.press('Tab')
  assert value()=='See \\ref{sec:intro}'
  text='\\label{sec:draft}\nSee \\autoref{sec:dr}'
  set_text(text,len(text)-1)
  expect(page.locator('.completion-key')).to_have_text('sec:draft')
  assert any('sec:draft' in request.get('buffers',{}).get('section.tex','') for request in requests)
  page.keyboard.press('Enter')
  assert value()=='\\label{sec:draft}\nSee \\autoref{sec:draft}'
  text='\\eqref{eq:}'
  set_text(text,len(text)-1)
  expect(page.locator('.completion-key')).to_have_text('eq:main')
  page.keyboard.press('Enter')
  assert value()=='\\eqref{eq:main}'
  print('PASS: ref/eqref/autoref, source location metadata, and unsaved label definitions')
  set_text('\\cite{}',len('\\cite{'))
  expect(page.locator('.completion-option')).to_have_count(3)
  page.keyboard.press('ArrowDown');page.keyboard.press('PageUp');page.keyboard.press('Enter')
  assert value() in ('\\cite{alpha2023}','\\cite{lee2025}','\\cite{smith2024}')
  set_text('\\cite{}',len('\\cite{'))
  expect(page.locator('#latex-completion')).to_be_visible()
  page.keyboard.press('Escape')
  page.wait_for_timeout(200)
  expect(page.locator('#latex-completion')).not_to_be_visible()
  page.keyboard.press('Control+Space')
  expect(page.locator('#latex-completion')).to_be_visible()
  set_text('% \\cite{sm')
  expect(page.locator('#latex-completion')).not_to_be_visible()
  set_text('Normal paragraph text.')
  expect(page.locator('#latex-completion')).not_to_be_visible()
  set_text('\\cite{does-not-exist}')
  page.evaluate('()=>{const cm=document.querySelector(".CodeMirror").CodeMirror;cm.setCursor(cm.posFromIndex(cm.getValue().length-1))}')
  expect(page.locator('.completion-empty')).to_contain_text('일치하는 참고문헌')
  page.keyboard.press('Enter')
  assert '\n' in value(),'Empty popup must keep normal Enter behavior'
  print('PASS: keyboard navigation, Escape, Ctrl+Space refresh, comments ignored and empty-result behavior')
  page.set_viewport_size({'width':390,'height':844})
  set_text('\\cite{sm}',len('\\cite{sm'))
  expect(page.locator('.completion-key')).to_have_text('smith2024')
  box=page.locator('#latex-completion').bounding_box()
  assert box['x']>=0 and box['x']+box['width']<=390 and box['y']>=0 and box['y']+box['height']<=844
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  out=Path(__file__).parent/'artifacts';out.mkdir(exist_ok=True)
  page.screenshot(path=out/'completion-mobile.png',full_page=True)
  assert not errors,errors
  print('PASS: mobile completion popup fits the viewport; no JavaScript errors')
  browser.close()
finally:
 app.shutdown();app.server_close();thread.join();fixture.tearDown()

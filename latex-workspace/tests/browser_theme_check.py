"""Theme persistence, OS defaults, popup synchronization, and editor integrations."""
import importlib.util
import os
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright, expect

spec=importlib.util.spec_from_file_location('theme_ai_tests',Path(__file__).with_name('test_ai_assistant.py'))
tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)
fixture=tests.AssistantTests();fixture.setUp()
(fixture.project/'references.bib').write_text('@article{smith2024,title={A Synthetic Study},author={Smith, Jane},year={2024}}')
server=tests.server
app=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);app.workspace=fixture.workspace
thread=threading.Thread(target=app.serve_forever,daemon=True);thread.start()
base=f'http://127.0.0.1:{app.server_port}'


def contrast(page,foreground,background):
    return page.evaluate('''([fg,bg])=>{
      const rgb=(value)=>value.match(/[\\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});
      const luminance=(values)=>.2126*values[0]+.7152*values[1]+.0722*values[2];
      const a=luminance(rgb(getComputedStyle(document.querySelector(fg)).color));
      const b=luminance(rgb(getComputedStyle(document.querySelector(bg)).backgroundColor));
      return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    }''',[foreground,background])


try:
 with sync_playwright() as p:
  options={'headless':True}
  if os.environ.get('FOLIO_BROWSER'):options['executable_path']=os.environ['FOLIO_BROWSER']
  browser=p.chromium.launch(**options)
  context=browser.new_context(viewport={'width':1500,'height':1020},color_scheme='dark')
  page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
  page.goto(base)
  expect(page.locator('html')).to_have_attribute('data-theme','dark')
  expect(page.locator('#theme-toggle')).to_have_attribute('aria-pressed','true')
  expect(page.locator('#ai-send')).to_be_enabled(timeout=15000)
  expect(page.locator('.pdf-page.rendered')).to_have_count(1,timeout=15000)
  assert page.locator('.pdf-page').evaluate('(el)=>getComputedStyle(el).backgroundColor')=='rgb(255, 255, 255)'
  assert contrast(page,'.CodeMirror','.CodeMirror')>=4.5
  print('PASS: OS dark preference applies before paint; editor has readable contrast; PDF paper remains white')
  page.locator('#theme-toggle').click()
  expect(page.locator('html')).to_have_attribute('data-theme','light')
  assert page.evaluate('localStorage.getItem("folio:theme")')=='light'
  page.reload()
  expect(page.locator('html')).to_have_attribute('data-theme','light')
  expect(page.locator('#ai-send')).to_be_enabled(timeout=15000)
  with page.expect_popup() as popup_info:page.locator('#popout').click()
  popup=popup_info.value;popup.on('pageerror',lambda error:errors.append(str(error)))
  expect(popup.locator('html')).to_have_attribute('data-theme','light')
  page.locator('#theme-toggle').click()
  expect(popup.locator('html')).to_have_attribute('data-theme','dark')
  popup.locator('#theme-toggle').click()
  expect(page.locator('html')).to_have_attribute('data-theme','light')
  popup.close();page.locator('#theme-toggle').click()
  print('PASS: explicit theme survives reload and synchronizes both directions with the PDF window')
  page.locator('#ai-settings').click()
  expect(page.locator('#ai-settings-form button[type="submit"]')).to_be_enabled()
  assert contrast(page,'#ai-settings-dialog','#ai-settings-dialog')>=4.5
  assert contrast(page,'#ai-model-input','#ai-model-input')>=4.5
  page.locator('#ai-settings-close').click()
  page.locator('button.tree-item[data-path="section.tex"]').click()
  expect(page.locator('#breadcrumb')).to_have_text('section.tex')
  source='A synthetic sentence for theme verification.'
  page.evaluate('(text)=>{const cm=document.querySelector(".CodeMirror").CodeMirror;cm.setValue(text);cm.setSelection({line:0,ch:0},{line:0,ch:text.length});cm.focus()}',source)
  page.locator('#add-comment').click()
  expect(page.locator('#comment-dialog')).to_be_visible()
  assert contrast(page,'#comment-body','#comment-body')>=4.5
  page.locator('#comment-body').fill('Clarify this synthetic sentence.')
  page.locator('#comment-submit').click()
  expect(page.locator('.comment-card')).to_have_count(1)
  assert contrast(page,'.comment-body','.comment-card')>=4.5
  page.locator('#comments-close').click()
  page.evaluate('()=>{const cm=document.querySelector(".CodeMirror").CodeMirror;cm.setValue("\\\\cite{sm}");cm.setCursor({line:0,ch:8});cm.focus()}')
  expect(page.locator('.completion-key')).to_have_text('smith2024')
  assert contrast(page,'.completion-key','.completion-option.active')>=4.5
  page.keyboard.press('Enter')
  page.locator('#ai-input').fill('inspect only');page.locator('#ai-send').click()
  expect(page.locator('#ai-messages')).to_contain_text('현재 파일 문맥을 확인했습니다.',timeout=15000)
  assert contrast(page,'.ai-message.user .ai-message-text','.ai-message.user')>=4.5
  output=Path(__file__).parent/'artifacts';output.mkdir(exist_ok=True)
  page.screenshot(path=output/'theme-dark-desktop.png',full_page=True)
  print('PASS: dark AI settings/chat, comments, and citation completion retain readable contrast and normal behavior')
  page.set_viewport_size({'width':390,'height':844})
  expect(page.locator('#theme-toggle')).to_be_visible()
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  page.locator('#theme-toggle').click()
  expect(page.locator('html')).to_have_attribute('data-theme','light')
  page.locator('#theme-toggle').click()
  page.screenshot(path=output/'theme-dark-mobile.png',full_page=True)
  print('PASS: mobile theme toggle remains accessible and fits the viewport')
  system_context=browser.new_context(color_scheme='light')
  system_page=system_context.new_page();system_page.goto(base)
  expect(system_page.locator('html')).to_have_attribute('data-theme','light')
  system_page.emulate_media(color_scheme='dark')
  expect(system_page.locator('html')).to_have_attribute('data-theme','dark')
  system_page.locator('#theme-toggle').click()
  system_page.emulate_media(color_scheme='light');system_page.emulate_media(color_scheme='dark')
  expect(system_page.locator('html')).to_have_attribute('data-theme','light')
  blocked=browser.new_context(color_scheme='dark')
  blocked.add_init_script('Storage.prototype.getItem=function(){throw new Error("blocked")};Storage.prototype.setItem=function(){throw new Error("blocked")};')
  blocked_page=blocked.new_page();blocked_page.on('pageerror',lambda error:errors.append(str(error)))
  blocked_page.goto(base)
  expect(blocked_page.locator('html')).to_have_attribute('data-theme','dark')
  blocked_page.locator('#theme-toggle').click()
  expect(blocked_page.locator('html')).to_have_attribute('data-theme','light')
  assert not errors,errors
  print('PASS: OS changes follow system default, manual choice wins, blocked storage works, no JavaScript errors')
  browser.close()
finally:
 app.shutdown();app.server_close();thread.join();fixture.tearDown()

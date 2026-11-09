"""Real two-page PDF, forward/reverse navigation, and sentence-comment lifecycle."""
import importlib.util
import os
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright, expect

spec=importlib.util.spec_from_file_location('annotation_ai_tests',Path(__file__).with_name('test_ai_assistant.py'))
tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)
fixture=tests.AssistantTests();fixture.setUp()
fixture.workspace.ai=fixture.ai
(fixture.project/'main.tex').write_text('\\documentclass{article}\n\\begin{document}\n\\input{section}\n\\end{document}\n')
source='\\section{Introduction}\nFirst sentence on the first page.\n\n\\newpage\nTarget sentence on the second page.\n\nA final sentence for context.\n'
(fixture.project/'section.tex').write_text(source)
job=fixture.workspace.start_compile({'project':fixture.key,'main':'main.tex'})
import time
while fixture.workspace.job_status(job['id'])['status']=='running':time.sleep(.05)
assert fixture.workspace.job_status(job['id'])['status']=='success'
server=tests.server
app=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);app.workspace=fixture.workspace
thread=threading.Thread(target=app.serve_forever,daemon=True);thread.start()
try:
    with sync_playwright() as p:
        options={'headless':True}
        if os.environ.get('FOLIO_BROWSER'):options['executable_path']=os.environ['FOLIO_BROWSER']
        browser=p.chromium.launch(**options)
        page=browser.new_page(viewport={'width':1500,'height':1020},device_scale_factor=2)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{app.server_port}')
        expect(page.locator('#page-total')).to_have_text('2',timeout=15000)
        page.locator('button.tree-item[data-path="section.tex"]').click()
        expect(page.locator('#breadcrumb')).to_have_text('section.tex')
        def click_source(line=4,ch=8):
            coords=page.evaluate('([line,ch])=>{const cm=document.querySelector(".CodeMirror").CodeMirror;cm.scrollIntoView({line,ch},50);const r=cm.charCoords({line,ch},"window");return {x:r.left+2,y:(r.top+r.bottom)/2}}',[line,ch])
            page.mouse.dblclick(coords['x'],coords['y'])
        for zoom in ['0.75','1.5','fit']:
            page.locator('#pdf-zoom').select_option(zoom)
            # Wait for the current zoom layout before triggering a navigation request.
            page.wait_for_timeout(250)
            click_source()
            expect(page.locator('#page-number')).to_have_value('2')
            expect(page.locator('.pdf-page[data-page="2"] .pdf-location-highlight')).to_be_visible()
            assert page.evaluate('document.querySelector(".CodeMirror").CodeMirror.getSelection()')=='Target sentence on the second page.'
            rect=page.locator('.pdf-location-highlight').bounding_box();container=page.locator('#pdf-container').bounding_box()
            assert rect['y']>=container['y'] and rect['y']<container['y']+container['height']
        print('PASS: editor double-click selects the sentence and reveals the exact PDF page at 75%, 150%, fit, HiDPI')
        # Reverse SyncTeX remains available.
        page.locator('.pdf-page[data-page="2"] .textLayer span').filter(has_text='Target sentence').first.dblclick()
        expect(page.locator('.source-jump-line')).to_have_count(1)
        click_source()
        page.locator('#add-comment').click()
        expect(page.locator('#comment-dialog')).to_be_visible()
        expect(page.locator('#comment-quote')).to_have_text('Target sentence on the second page.')
        page.locator('#comment-body').fill('이 문장의 근거를 보완해주세요. <script>alert(1)</script>')
        page.locator('#comment-submit').click()
        expect(page.locator('#comment-dialog')).not_to_be_visible()
        expect(page.locator('.comment-card')).to_have_count(1)
        expect(page.locator('.comment-anchor')).to_have_count(1)
        assert (fixture.project/'section.tex').read_text()==source
        panel=page.locator('#comments-panel').bounding_box();files=page.locator('#sidebar').bounding_box();code=page.locator('#editor-panel').bounding_box()
        assert panel['x']>=files['x']+files['width'] and panel['x']+panel['width']<=code['x']+1
        page.locator('#toggle-comments').click()
        expect(page.locator('#comments-panel')).not_to_be_visible()
        page.locator('#toggle-comments').click()
        expect(page.locator('#comments-panel')).to_be_visible()
        print('PASS: comments dock between file list and editor; toolbar toggles visibility')
        page.locator('#comments-close').click()
        page.locator('.comment-anchor').first.click()
        expect(page.locator('#comments-panel')).to_be_visible()
        expect(page.locator('#comments-list')).to_contain_text('이 문장의 근거를 보완해주세요.')
        page.reload()
        expect(page.locator('#breadcrumb')).to_have_text('section.tex')
        expect(page.locator('#toggle-comments .comment-count')).to_have_text('1')
        expect(page.locator('#comments-panel')).to_be_visible()
        expect(page.locator('#comments-list')).to_contain_text('<script>alert(1)</script>')
        page.get_by_role('button',name='수정',exact=True).click()
        page.locator('#comment-body').fill('수정한 코멘트입니다.')
        page.locator('#comment-submit').click()
        expect(page.locator('#comments-list')).to_contain_text('수정한 코멘트입니다.')
        print('PASS: comments save separately, survive reload, safely display text, and can be edited')
        page.get_by_role('button',name='해결',exact=True).click()
        expect(page.locator('.comment-card')).to_have_count(0)
        page.locator('#comments-resolved').check()
        expect(page.locator('.comment-card.resolved')).to_have_count(1)
        page.get_by_role('button',name='다시 열기',exact=True).click()
        expect(page.locator('#toggle-comments .comment-count')).to_have_text('1')
        page.locator('.comment-source').click()
        expect(page.locator('#comments-panel')).to_be_visible()
        page.locator('#comments-close').click()
        assert page.evaluate('document.querySelector(".CodeMirror").CodeMirror.getSelection()')=='Target sentence on the second page.'
        # Insert before the anchor; saved anchors and old PDF coordinates must both shift correctly.
        page.evaluate('()=>document.querySelector(".CodeMirror").CodeMirror.replaceRange("% inserted\\n",{line:0,ch:0})')
        page.wait_for_timeout(1400)
        page.locator('#toggle-comments').click()
        expect(page.locator('.comment-card-heading')).to_contain_text('6행')
        page.locator('#comments-close').click()
        click_source(5)
        expect(page.locator('.pdf-page[data-page="2"] .pdf-location-highlight')).to_be_visible()
        expect(page.locator('#toast')).to_contain_text('마지막 컴파일 기준')
        # A changed sentence is never silently mapped onto an unrelated PDF paragraph.
        page.evaluate('()=>{const cm=document.querySelector(".CodeMirror").CodeMirror;cm.replaceRange("New uncompiled claim.",{line:5,ch:0},{line:5,ch:cm.getLine(5).length})}')
        click_source(5)
        expect(page.locator('#toast')).to_contain_text('다시 컴파일')
        page.wait_for_timeout(1300)
        page.locator('#toggle-comments').click()
        expect(page.locator('.comment-state')).to_contain_text('원문 위치 확인 필요')
        expect(page.locator('#comments-list')).to_contain_text('수정한 코멘트입니다.')
        print('PASS: inserted lines remap correctly; changed source keeps comments and requests recompilation')
        output=Path(__file__).parent/'artifacts';output.mkdir(exist_ok=True)
        page.screenshot(path=output/'sentence-comments.png',full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        expect(page.locator('#comments-panel')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=output/'sentence-comments-mobile.png',full_page=True)
        page.get_by_role('button',name='삭제',exact=True).click()
        expect(page.locator('.comment-card')).to_have_count(0)
        assert fixture.workspace.comments.list(fixture.key)==[]
        assert not errors,errors
        print('PASS: comments resolve/reopen/delete, fit mobile, no JavaScript errors')
        browser.close()
finally:
    app.shutdown();app.server_close();thread.join();fixture.tearDown()

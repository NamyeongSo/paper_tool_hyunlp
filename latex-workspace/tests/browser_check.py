"""End-to-end checks in an isolated temporary paper, leaving the user's paper intact."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
from playwright.sync_api import sync_playwright, expect

spec = importlib.util.spec_from_file_location('folio_tests', Path(__file__).with_name('test_workspace.py'))
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
server = tests.server

with tempfile.TemporaryDirectory(prefix='folio-browser-') as temporary:
    root = Path(temporary)
    project = root / 'paper'
    (project / 'sections').mkdir(parents=True)
    (project / 'main.tex').write_text(tests.MAIN)
    section = project / 'sections/introduction.tex'
    section.write_text('Original paper text.\n')
    second = root / 'second-paper'
    second.mkdir()
    (second / 'main.tex').write_text(tests.MAIN.replace('Test Paper', 'Second Paper').replace('\\input{sections/introduction}', 'Second project.'))
    http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    http.workspace = server.Workspace(root)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            options = {'headless': True}
            binary = os.environ.get('FOLIO_BROWSER')
            if binary: options['executable_path'] = binary
            browser = p.chromium.launch(**options)
            page = browser.new_page(viewport={'width': 1500, 'height': 960}, device_scale_factor=2)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{http.server_port}')
            expect(page.locator('.pdf-page.rendered')).to_have_count(1, timeout=30000)
            print('PASS: project opens and real PDF renders')
            page.locator('button.tree-item[data-path="sections"]').click()
            page.locator('button.tree-item[data-path="sections/introduction.tex"]').click()
            expect(page.locator('#breadcrumb')).to_have_text('sections  /  introduction.tex')
            for zoom in ['0.75', '1.5', 'fit']:
                page.locator('button.tree-item[data-path="main.tex"]').click()
                page.locator('#pdf-zoom').select_option(zoom)
                page.locator('.textLayer span').filter(has_text='Original paper text.').first.dblclick()
                expect(page.locator('#breadcrumb')).to_have_text('sections  /  introduction.tex')
                expect(page.locator('.source-jump-line')).to_have_count(1)
                assert page.evaluate("document.querySelector('.CodeMirror').CodeMirror.getCursor().line") == 0
            print('PASS: PDF double-click opens included source at 75%, 150% and fit width on a HiDPI screen')
            page.evaluate("document.querySelector('.CodeMirror').CodeMirror.setValue('Saved from browser.\\n')")
            expect(page.locator('#save-state')).to_contain_text('모든 변경사항 저장됨', timeout=10000)
            assert section.read_text() == 'Saved from browser.\n'
            print('PASS: folder navigation, syntax editor and disk autosave')
            section.write_text('External text.\n')
            page.evaluate("document.querySelector('.CodeMirror').CodeMirror.setValue('Browser conflict copy.\\n')")
            expect(page.locator('#conflict-dialog')).to_be_visible(timeout=10000)
            with page.expect_download() as download_info:
                page.locator('#download-draft').click()
            assert Path(download_info.value.path()).read_text() == 'Browser conflict copy.\n'
            page.locator('#reload-conflict').click()
            expect(page.locator('#conflict-dialog')).not_to_be_visible()
            assert page.evaluate("document.querySelector('.CodeMirror').CodeMirror.getValue()") == 'External text.\n'
            page.evaluate("document.querySelector('.CodeMirror').CodeMirror.setValue('Saved from browser.\\n')")
            expect(page.locator('#save-state')).to_contain_text('모든 변경사항 저장됨', timeout=10000)
            print('PASS: save conflict protects disk and supports draft download/reload')
            with page.expect_popup() as popup_info:
                page.locator('#popout').click()
            popup = popup_info.value
            popup.on('pageerror', lambda error: errors.append(str(error)))
            expect(popup.locator('.pdf-page.rendered')).to_have_count(1, timeout=15000)
            page.locator('#compile').click()
            expect(page.locator('#compile-detail')).to_contain_text('컴파일 완료', timeout=30000)
            expect(popup.locator('.textLayer')).to_contain_text('Saved from browser.', timeout=20000)
            print('PASS: pop-out PDF updates after recompilation')
            page.locator('button.tree-item[data-path="main.tex"]').click()
            popup.locator('.textLayer span').filter(has_text='Saved from browser.').first.dblclick()
            expect(page.locator('#breadcrumb')).to_have_text('sections  /  introduction.tex')
            expect(popup.locator('#state')).to_contain_text('sections/introduction.tex')
            expect(page.locator('.source-jump-line')).to_have_count(1)
            print('PASS: double-click in the separate PDF window navigates the original editor')
            page.evaluate("document.querySelector('.CodeMirror').CodeMirror.setValue('\\\\undefinedFolioCommand\\n')")
            page.keyboard.press('Control+Enter')
            expect(page.locator('#logs-panel')).to_be_visible(timeout=30000)
            expect(page.locator('#diagnostics')).to_contain_text('Undefined control sequence')
            page.locator('#diagnostics button').first.click()
            assert page.evaluate("document.querySelector('.CodeMirror').CodeMirror.getCursor().line") == 0
            assert page.locator('.pdf-page.rendered').count() == 1
            print('PASS: compiler error links to source and previous PDF survives')
            page.locator('#close-logs').click()
            page.evaluate("document.querySelector('.CodeMirror').CodeMirror.setValue('Fixed paper.\\n')")
            page.keyboard.press('Control+s')
            expect(page.locator('#save-state')).to_contain_text('모든 변경사항 저장됨')
            page.locator('#new-file').click()
            page.locator('#entry-path').fill('sections/discussion.tex')
            page.locator('#new-form button[type="submit"]').click()
            expect(page.locator('#breadcrumb')).to_have_text('sections  /  discussion.tex')
            assert (project / 'sections/discussion.tex').exists()
            print('PASS: creates and opens a nested LaTeX file')
            page.locator('#file-search').fill('discussion')
            expect(page.locator('#file-tree')).to_contain_text('discussion.tex')
            assert page.locator('#file-tree button').count() == 2
            page.locator('#file-search').clear()
            handle = page.locator('.editor-resizer').bounding_box()
            before = page.locator('#editor-panel').bounding_box()['width']
            page.mouse.move(handle['x'] + 2, 350); page.mouse.down(); page.mouse.move(handle['x'] - 70, 350); page.mouse.up()
            assert page.locator('#editor-panel').bounding_box()['width'] < before - 40
            print('PASS: file filtering and pane resizing')
            page.locator('#project-picker').click()
            page.locator('.project-card', has_text='Second Paper').click()
            expect(page.locator('#project-title')).to_have_text('Second Paper')
            expect(page.locator('.pdf-page.rendered')).to_have_count(1, timeout=30000)
            expect(page.locator('.textLayer')).to_contain_text('Second project.')
            print('PASS: switches projects and renders the corresponding PDF')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.wait_for_timeout(500)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.locator('#toggle-sidebar').click()
            expect(page.locator('#sidebar')).to_be_visible()
            page.locator('button.tree-item[data-path="main.tex"]').click()
            expect(page.locator('#sidebar')).not_to_be_visible()
            assert not errors, errors
            print('PASS: mobile layout, no JavaScript errors')
            browser.close()
    finally:
        http.shutdown(); http.server_close(); thread.join()

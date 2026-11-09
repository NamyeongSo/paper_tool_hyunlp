"""Exercise AI chat against a deterministic local model, using temporary papers."""
import importlib.util
import json
import os
import re
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright, expect

spec = importlib.util.spec_from_file_location('assistant_tests', Path(__file__).with_name('test_ai_assistant.py'))
tests = importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
server = tests.server

with tempfile.TemporaryDirectory(prefix='folio-ai-browser-') as temporary:
    root = Path(temporary); project = root / 'paper'; project.mkdir()
    (project / 'main.tex').write_text('\\documentclass{article}\n\\begin{document}\n\\input{section}\n\\end{document}\n')
    section = project / 'section.tex'; section.write_text('The evaluation of model output was carried out by us.\n\n')
    model = tests.ThreadingHTTPServer(('127.0.0.1', 0), tests.FakeModel); model.calls = []
    model.pause_stream = True; model.partial_ready = threading.Event(); model.release_partial = threading.Event()
    model.stream_delay = .004
    model_thread = threading.Thread(target=model.serve_forever, daemon=True); model_thread.start()
    config = root / 'config.json'
    tests.persist(config, {'port': model.server_port, 'model': 'TestWriter', 'context_window': 4096, 'max_output_tokens': 512, 'summary_tokens': 256})
    app = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    app.workspace = server.Workspace(root, ai_config_path=config)
    # A short test prompt lets the fake model's small context exercise compaction.
    prompt = root / 'system.txt'; prompt.write_text('Be a scientific editor. Preserve facts and citations.')
    app.workspace.ai.prompt_path = prompt
    app_thread = threading.Thread(target=app.serve_forever, daemon=True); app_thread.start()
    try:
        with sync_playwright() as p:
            options = {'headless': True}
            if os.environ.get('FOLIO_BROWSER'): options['executable_path'] = os.environ['FOLIO_BROWSER']
            browser = p.chromium.launch(**options)
            page = browser.new_page(viewport={'width': 1500, 'height': 960})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            streams = []
            page.on('response', lambda response: streams.append(response.headers.get('content-type', '')) if '/api/ai/stream?' in response.url else None)
            base = f'http://127.0.0.1:{app.server_port}'
            page.goto(base)
            expect(page.locator('#ai-send')).to_be_enabled(timeout=15000)
            page.locator('button.tree-item[data-path="section.tex"]').click()
            expect(page.locator('#breadcrumb')).to_have_text('section.tex')
            unsaved = 'The model output was evaluated by us.'
            page.evaluate('(text) => {const cm=document.querySelector(".CodeMirror").CodeMirror;cm.setValue(text);cm.setSelection({line:0,ch:0},{line:0,ch:text.length})}', unsaved)
            page.locator('#ai-input').fill('correct this selected sentence')
            page.locator('#ai-send').click()
            expect(page.locator('#ai-live-answer')).to_contain_text('검', timeout=20000)
            expect(page.locator('#ai-progress')).to_be_visible()
            assert '"action"' not in page.locator('#ai-live-answer').inner_text()
            assert 'text/event-stream; charset=utf-8' in streams
            page.reload()
            expect(page.locator('#ai-live-answer')).to_contain_text('검', timeout=15000)
            expect(page.locator('#ai-progress')).to_be_visible()
            print('PASS: SSE displays the answer before completion and reconnects after reload')
            model.release_partial.set()
            expect(page.locator('.ai-proposal')).to_have_count(1, timeout=20000)
            expect(page.locator('.ai-diff-old')).to_have_text(unsaved)
            expect(page.locator('.ai-file-tag')).to_contain_text('section.tex · 선택 영역')
            assert any('The model output was evaluated by us.' in message['content'] for message in model.calls[0]['messages'])
            print('PASS: chat sends active unsaved buffer and selection, then displays a ReAct proposal')
            page.get_by_role('button', name='편집기에 적용', exact=True).click()
            expect(page.locator('.ai-proposal-status')).to_have_text('적용됨')
            assert section.read_text() == 'We evaluated the model output.'
            assert page.evaluate('document.querySelector(".CodeMirror").CodeMirror.getValue()') == section.read_text()
            print('PASS: applying a proposal edits the buffer and saves the actual file')
            page.reload()
            expect(page.locator('.ai-proposal-status')).to_have_text('적용됨', timeout=15000)
            print('PASS: chat and applied proposal persist after reload')
            page.locator('#ai-settings').click()
            expect(page.locator('#ai-settings-form button[type="submit"]')).to_be_enabled()
            expect(page.locator('#ai-model-input')).to_have_value('TestWriter')
            expect(page.locator('#ai-stream')).to_be_checked()
            expect(page.locator('#ai-thinking')).not_to_be_checked()
            expect(page.locator('#ai-thinking-level')).to_be_disabled()
            page.locator('#ai-thinking').check()
            page.locator('#ai-thinking-level').select_option('high')
            expect(page.locator('#ai-thinking-help')).to_contain_text('384 토큰')
            page.locator('#ai-threshold').fill('60')
            page.locator('#ai-settings-form button[type="submit"]').click()
            expect(page.locator('#ai-settings-dialog')).not_to_be_visible()
            assert json.loads(config.read_text())['compaction_threshold'] == .6
            assert json.loads(config.read_text())['chat_template_kwargs']['enable_thinking'] is True
            assert json.loads(config.read_text())['thinking_level'] == 'high'
            print('PASS: server-side port/model/context settings save and validate the model connection')
            page.locator('#ai-input').fill('inspect only')
            page.locator('#ai-send').click()
            expect(page.locator('#ai-progress')).not_to_be_visible(timeout=20000)
            assert model.calls[-1]['thinking_token_budget'] == 384
            assert model.calls[-1]['chat_template_kwargs']['enable_thinking'] is True
            page.locator('#ai-settings').click()
            expect(page.locator('#ai-settings-form button[type="submit"]')).to_be_enabled()
            expect(page.locator('#ai-thinking')).to_be_checked()
            expect(page.locator('#ai-thinking-level')).to_have_value('high')
            page.locator('#ai-thinking-level').select_option('custom')
            page.locator('#ai-thinking-budget').fill('500')
            assert page.locator('#ai-thinking-budget').evaluate('(element) => !element.checkValidity()')
            page.locator('#ai-thinking-budget').fill('128')
            page.locator('#ai-settings-form button[type="submit"]').click()
            expect(page.locator('#ai-settings-dialog')).not_to_be_visible()
            page.locator('#ai-input').fill('inspect only')
            page.locator('#ai-send').click()
            expect(page.locator('#ai-progress')).not_to_be_visible(timeout=20000)
            assert model.calls[-1]['thinking_token_budget'] == 128
            print('PASS: Thinking toggle, high/custom budgets, persistence and actual inference parameters')
            key = app.workspace.discover()[0]['id']
            session_id = page.evaluate('(key) => localStorage.getItem("folio:ai:session:"+key)', key)
            session = app.workspace.ai.load_session(key, session_id)
            for number in range(10):
                for role in ['user', 'assistant']:
                    item = {'id': f'{role}-{number}', 'role': role, 'content': 'Preserve facts and scientific intent. ' * 35}
                    session['messages'].append(item); session['model_history'].append({'role': role, 'content': item['content']})
            app.workspace.ai.save_session(session)
            page.reload(); expect(page.locator('#ai-send')).to_be_enabled(timeout=15000)
            page.locator('#ai-input').fill('inspect only')
            page.locator('#ai-send').click()
            expect(page.locator('#ai-memory')).to_contain_text(re.compile(r'요약 [1-9]\d*회'), timeout=20000)
            expect(page.locator('#ai-progress')).not_to_be_visible(timeout=20000)
            assert len(app.workspace.ai.load_session(key, session_id)['messages']) >= 24
            print('PASS: automatic compaction is visible while the full conversation remains stored')
            page.locator('#ai-toggle').click(); expect(page.locator('#ai-input')).not_to_be_visible()
            page.locator('#ai-toggle').click(); expect(page.locator('#ai-input')).to_be_visible()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            expect(page.locator('#ai-input')).to_be_visible()
            assert not errors, errors
            print('PASS: collapsible chat, mobile layout and no JavaScript errors')
            browser.close()
    finally:
        model.release_partial.set()
        app.shutdown(); app.server_close(); app_thread.join()
        model.shutdown(); model.server_close(); model_thread.join()

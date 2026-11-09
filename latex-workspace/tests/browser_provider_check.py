"""Verify provider switching and authenticated Responses traffic in the browser."""
import importlib.util
import json
import os
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright, expect

spec = importlib.util.spec_from_file_location('provider_tests', Path(__file__).with_name('test_openai_provider.py'))
tests = importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
fixture = tests.OpenAIProviderTests(); fixture.setUp()
server = tests.tests.server
app = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler); app.workspace = fixture.workspace
thread = threading.Thread(target=app.serve_forever, daemon=True); thread.start()
try:
    with sync_playwright() as p:
        options = {'headless': True}
        if os.environ.get('FOLIO_BROWSER'): options['executable_path'] = os.environ['FOLIO_BROWSER']
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={'width':1500,'height':960})
        errors=[]; page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{app.server_port}')
        expect(page.locator('#ai-send')).to_be_enabled(timeout=15000)
        page.locator('#ai-settings').click(); expect(page.locator('#ai-settings-form button[type="submit"]')).to_be_enabled()
        expect(page.locator('#ai-provider')).to_have_value('local')
        page.locator('#ai-provider').select_option('openai')
        expect(page.locator('#ai-base-url')).to_have_value('https://api.openai.com/v1')
        expect(page.locator('#ai-model-input')).to_have_value('gpt-6-luna')
        expect(page.locator('#ai-port')).not_to_be_visible()
        page.locator('#ai-provider').select_option('local')
        expect(page.locator('#ai-model-input')).to_have_value('TestWriter')
        expect(page.locator('#ai-port')).to_have_value(str(fixture.model.server_port))
        print('PASS: OpenAI defaults fill automatically; Localhost model/port are restored')
        page.locator('#ai-provider').select_option('openai')
        page.locator('#ai-base-url').fill(fixture.base)
        page.locator('#ai-api-key').fill(tests.KEY)
        page.locator('#ai-thinking').check(); page.locator('#ai-thinking-level').select_option('high')
        expect(page.locator('#ai-thinking-custom-field')).not_to_be_visible()
        with page.expect_response(lambda response: '/api/ai/config' in response.url and response.request.method == 'PUT') as response:
            page.locator('#ai-settings-form button[type="submit"]').click()
        assert tests.KEY not in json.dumps(response.value.json())
        expect(page.locator('#ai-settings-dialog')).not_to_be_visible()
        expect(page.locator('#ai-model')).to_contain_text('OpenAI · gpt-6-luna')
        assert tests.KEY not in page.evaluate('JSON.stringify(localStorage)')
        assert tests.KEY not in fixture.config_path.read_text()
        print('PASS: provider, URL, model and API key save without exposing credentials')
        page.locator('#ai-input').fill('inspect only'); page.locator('#ai-send').click()
        expect(page.locator('#ai-messages')).to_contain_text('OpenAI 연결과 현재 파일 문맥을 확인했습니다.', timeout=20000)
        expect(page.locator('#ai-progress')).not_to_be_visible()
        path, body = fixture.openai.requests[-1]
        assert path == '/v1/responses' and body['model']=='gpt-6-luna'
        assert body['reasoning']=={'effort':'high'} and body['stream'] is True
        print('PASS: chat uses the OpenAI Responses endpoint with native reasoning and streaming')
        page.reload(); expect(page.locator('#ai-send')).to_be_enabled(timeout=15000)
        expect(page.locator('#ai-model')).to_contain_text('OpenAI · gpt-6-luna')
        page.locator('#ai-settings').click(); expect(page.locator('#ai-settings-form button[type="submit"]')).to_be_enabled()
        expect(page.locator('#ai-api-key')).to_have_value('')
        expect(page.locator('#ai-key-help')).to_contain_text('저장되어')
        page.locator('#ai-provider').select_option('local')
        expect(page.locator('#ai-model-input')).to_have_value('TestWriter')
        page.locator('#ai-settings-form button[type="submit"]').click(); expect(page.locator('#ai-settings-dialog')).not_to_be_visible()
        page.locator('#ai-input').fill('inspect only'); page.locator('#ai-send').click()
        expect(page.locator('#ai-progress')).not_to_be_visible(timeout=20000)
        assert fixture.model.calls[-1]['model']=='TestWriter'
        assert fixture.ai.config()['openai_model']=='gpt-6-luna'
        print('PASS: provider persists after reload and switches back to vLLM inference')
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#ai-settings').click(); expect(page.locator('#ai-settings-form button[type="submit"]')).to_be_enabled()
        page.locator('#ai-provider').select_option('openai')
        expect(page.locator('#ai-base-url')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors,errors
        print('PASS: provider settings fit mobile; no JavaScript errors')
        browser.close()
finally:
    app.shutdown(); app.server_close(); thread.join(); fixture.tearDown()

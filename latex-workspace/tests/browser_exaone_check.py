"""Exercise EXAONE settings and chat with a local fake API and temporary paper."""
import importlib.util
import json
import os
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright, expect

spec = importlib.util.spec_from_file_location('exaone_tests', Path(__file__).with_name('test_exaone_provider.py'))
tests = importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
fixture = tests.ExaoneProviderTests(); fixture.setUp()
server = tests.tests.server
fixture.ai.store_api_key(tests.OPENAI_KEY,dict(fixture.ai.config(),provider='openai'))
app = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);app.workspace=fixture.workspace
thread = threading.Thread(target=app.serve_forever,daemon=True);thread.start()
try:
    with sync_playwright() as p:
        options={'headless':True}
        if os.environ.get('FOLIO_BROWSER'):options['executable_path']=os.environ['FOLIO_BROWSER']
        browser=p.chromium.launch(**options)
        page=browser.new_page(viewport={'width':1500,'height':960})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        # Keep the production URL in the UI; route only test configuration to the fake API.
        def route_config(route):
            if route.request.method=='PUT':
                body=route.request.post_data_json
                if body.get('provider')=='exaone':body['exaone_base_url']=fixture.base
                route.continue_(post_data=json.dumps(body))
            else:route.continue_()
        page.route('**/api/ai/config',route_config)
        page.goto(f'http://127.0.0.1:{app.server_port}')
        expect(page.locator('#ai-send')).to_be_enabled(timeout=15000)
        page.locator('#ai-settings').click()
        expect(page.locator('#ai-settings-form button[type="submit"]')).to_be_enabled()
        page.locator('#ai-provider').select_option('exaone')
        expect(page.locator('#ai-base-url')).to_have_value('https://api.exaone.ai/v1')
        expect(page.locator('#ai-model-input')).to_have_value('k-exaone_v2.5')
        expect(page.locator('#ai-port')).not_to_be_visible()
        expect(page.locator('#ai-thinking')).to_be_checked()
        expect(page.locator('#ai-thinking')).to_be_disabled()
        assert page.locator('#ai-thinking-level option').evaluate_all('(items)=>items.map(item=>item.value)')==['low','high','max']
        expect(page.locator('#ai-thinking-custom-field')).not_to_be_visible()
        page.locator('#ai-api-key').fill(tests.KEY)
        page.locator('#ai-provider').select_option('openai')
        expect(page.locator('#ai-api-key')).to_have_value('')
        expect(page.locator('#ai-base-url')).to_have_value('https://api.openai.com/v1')
        expect(page.locator('#ai-key-help')).to_contain_text('저장되어')
        page.locator('#ai-provider').select_option('local')
        expect(page.locator('#ai-model-input')).to_have_value('TestWriter')
        expect(page.locator('#ai-thinking')).not_to_be_checked()
        print('PASS: official EXAONE defaults, native reasoning controls, provider/key separation')
        page.locator('#ai-provider').select_option('exaone')
        page.locator('#ai-thinking-level').select_option('max')
        page.locator('#ai-api-key').fill(tests.KEY)
        with page.expect_response(lambda response:'/api/ai/config' in response.url and response.request.method=='PUT') as config_response:
            page.locator('#ai-settings-form button[type="submit"]').click()
        expect(page.locator('#ai-settings-dialog')).not_to_be_visible()
        expect(page.locator('#ai-model')).to_contain_text('EXAONE · k-exaone_v2.5')
        assert tests.KEY not in json.dumps(config_response.value.json())
        assert tests.KEY not in page.evaluate('JSON.stringify(localStorage)')
        assert tests.KEY not in fixture.config_path.read_text()
        assert fixture.ai.api_key_path.read_text()==tests.OPENAI_KEY
        assert fixture.ai.exaone_api_key_path.read_text()==tests.KEY
        assert fixture.ai.exaone_api_key_path.stat().st_mode & 0o777==0o600
        calls=len(fixture.exaone.requests)
        page.reload();expect(page.locator('#ai-send')).to_be_enabled(timeout=15000)
        assert len(fixture.exaone.requests)==calls
        print('PASS: key submitted via web, protected on server, no billable probe on reload')
        page.locator('#ai-input').fill('inspect only');page.locator('#ai-send').click()
        expect(page.locator('#ai-messages')).to_contain_text('EXAONE 연결과 현재 문맥을 확인했습니다.',timeout=20000)
        expect(page.locator('#ai-progress')).not_to_be_visible()
        path,body=fixture.exaone.requests[-1]
        assert path=='/v1/chat/completions' and body['model']=='k-exaone_v2.5'
        assert body['reasoning_effort']=='max' and body['stream'] is True
        assert 'PRIVATE_REASONING' not in page.locator('#ai-messages').inner_text()
        print('PASS: browser chat uses EXAONE streaming Chat Completions and native effort')
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#ai-settings').click();expect(page.locator('#ai-settings-form button[type="submit"]')).to_be_enabled()
        expect(page.locator('#ai-api-key')).to_have_value('')
        expect(page.locator('#ai-key-help')).to_contain_text('저장되어')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        path=Path(__file__).parents[1]/'tests'/'artifacts';path.mkdir(exist_ok=True)
        page.screenshot(path=path/'exaone-mobile.png',full_page=True)
        page.set_viewport_size({'width':1500,'height':960})
        page.screenshot(path=path/'exaone-settings.png',full_page=True)
        page.locator('#ai-clear-key').check()
        page.locator('#ai-settings-form button[type="submit"]').click()
        expect(page.locator('#ai-settings-error')).to_contain_text('EXAONE API 키')
        assert not fixture.ai.exaone_api_key_path.exists()
        assert fixture.ai.api_key_path.read_text()==tests.OPENAI_KEY
        assert not errors,errors
        print('PASS: mobile settings, web key deletion, other provider preserved, no JavaScript errors')
        browser.close()
finally:
    app.shutdown();app.server_close();thread.join();fixture.tearDown()

"""Wire-format tests for OpenAI Responses, without sending user data to a cloud API."""
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('local_ai_tests', Path(__file__).with_name('test_ai_assistant.py'))
tests = importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
KEY = 'test-openai-credential-not-a-real-key'


class FakeOpenAI(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def authenticated(self):
        self.server.headers_seen.append(dict(self.headers))
        if self.headers.get('Authorization') != 'Bearer ' + KEY or getattr(self.server, 'reject_key', False):
            self.reply({'error': {'message': 'Invalid credential: ' + KEY}}, 401); return False
        return True

    def do_GET(self):
        if self.authenticated():
            self.reply({'data': [{'id': 'gpt-6-luna', 'object': 'model'}]})

    def do_POST(self):
        if not self.authenticated():
            return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, body))
        if self.path == '/v1/responses/input_tokens':
            self.reply({'object': 'response.input_tokens', 'input_tokens': tests.token_count(body['input'])}); return
        if self.path != '/v1/responses':
            self.reply({'error': {'message': 'Wrong OpenAI endpoint'}}, 404); return
        messages = body['input']
        if messages[0]['content'].startswith('Summarize editorial context'):
            answer = 'Preserve facts, reported numbers, citations and the author\'s editorial decisions.'
        else:
            current = next(item for item in messages if 'CURRENT EDITOR CONTEXT:\n' in item['content'])
            context = json.loads(current['content'].split('CURRENT EDITOR CONTEXT:\n', 1)[1])
            if 'inspect only' in current['content']:
                answer = json.dumps({'action': 'final', 'answer': 'OpenAI 연결과 현재 파일 문맥을 확인했습니다.'}, ensure_ascii=False)
            elif messages[-1]['content'].startswith('TOOL OBSERVATION:'):
                previous = json.loads(messages[-2]['content'])
                observation = json.loads(messages[-1]['content'].split('\n', 1)[1])
                if previous['action'] == 'read_file':
                    answer = json.dumps({'action': 'propose_edit', 'arguments': {'path': context['file'], 'old_text': observation['content'], 'new_text': 'We evaluated the model output.', 'explanation': '더 직접적인 문장입니다.'}}, ensure_ascii=False)
                else:
                    answer = json.dumps({'action': 'final', 'answer': '검토할 수정안을 제안했습니다.'}, ensure_ascii=False)
            else:
                answer = json.dumps({'action': 'read_file', 'arguments': {'path': context['file']}})
        response = {'id': 'resp_test', 'status': 'completed', 'model': body['model'], 'incomplete_details': None,
                    'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': answer}]}],
                    'usage': {'input_tokens': tests.token_count(messages), 'output_tokens': 30, 'output_tokens_details': {'reasoning_tokens': 5}}}
        if getattr(self.server, 'incomplete', False):
            response.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
        if not body.get('stream'):
            self.reply(response); return
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Connection', 'close'); self.end_headers(); self.close_connection = True
        def event(kind, **values):
            data = json.dumps({'type': kind, **values}, ensure_ascii=False)
            self.wfile.write(f'event: {kind}\ndata: {data}\n\n'.encode()); self.wfile.flush()
        try:
            event('response.created', response={'id': 'resp_test', 'status': 'in_progress'})
            if body.get('reasoning', {}).get('effort') != 'none':
                event('response.output_item.added', item={'type': 'reasoning', 'summary': []})
            for index in range(0, len(answer), 3):
                event('response.output_text.delta', delta=answer[index:index + 3], output_index=0, content_index=0)
                time.sleep(.003)
            event('response.incomplete' if response['status'] == 'incomplete' else 'response.completed', response=response)
        except (BrokenPipeError, ConnectionResetError):
            pass


class OpenAIProviderTests(unittest.TestCase):
    wait_run = tests.AssistantTests.wait_run
    start = tests.AssistantTests.start

    def setUp(self):
        tests.AssistantTests.setUp(self)
        self.env = patch.dict(os.environ, {'OPENAI_API_KEY': ''}); self.env.start()
        self.openai = ThreadingHTTPServer(('127.0.0.1', 0), FakeOpenAI)
        self.openai.requests, self.openai.headers_seen = [], []
        self.openai_thread = threading.Thread(target=self.openai.serve_forever, daemon=True); self.openai_thread.start()
        self.base = f'http://127.0.0.1:{self.openai.server_port}/v1'

    def tearDown(self):
        self.openai.shutdown(); self.openai.server_close(); self.openai_thread.join(); self.env.stop()
        tests.AssistantTests.tearDown(self)

    def use_openai(self, **values):
        return self.ai.update_config({'provider': 'openai', 'openai_base_url': self.base, 'api_key': KEY, **values})

    def test_defaults_profiles_and_key_protection(self):
        self.assertEqual(self.ai.config()['openai_base_url'], 'https://api.openai.com/v1')
        self.assertEqual(self.ai.config()['openai_model'], 'gpt-6-luna')
        settings = self.use_openai(context_window=0)
        self.assertTrue(settings['connected'])
        self.assertEqual(settings['active_model'], 'gpt-6-luna')
        self.assertEqual(settings['context_window'], 1050000)
        self.assertEqual(self.ai.config()['model'], 'TestWriter')
        self.assertNotIn(KEY, json.dumps(settings))
        self.assertNotIn(KEY, self.config_path.read_text())
        self.assertEqual(self.ai.api_key_path.stat().st_mode & 0o777, 0o600)
        local = self.ai.update_config({'provider': 'local'})
        self.assertTrue(local['connected']); self.assertEqual(local['active_model'], 'TestWriter')
        self.assertEqual(self.ai.config()['openai_model'], 'gpt-6-luna')
        self.assertNotIn('Authorization', self.model.calls[-1] if self.model.calls else {})

    def test_responses_stream_react_and_native_reasoning(self):
        self.use_openai(enable_thinking=True, openai_reasoning_effort='high')
        run = self.start()
        self.assertEqual(run['status'], 'success', run.get('error'))
        self.assertEqual(run['model'], 'gpt-6-luna'); self.assertEqual(run['provider'], 'openai')
        self.assertTrue(run['proposals']); self.assertEqual(run['thinking_level'], 'high')
        self.assertEqual((self.project / 'section.tex').read_text(), 'Disk text.')
        self.assertEqual(run['token_count_method'], 'openai')
        for path, body in self.openai.requests:
            self.assertNotIn('chat_template_kwargs', body); self.assertNotIn('thinking_token_budget', body)
            if path.endswith('/responses'):
                self.assertEqual(body['reasoning'], {'effort': 'high'})
                self.assertFalse(body['store']); self.assertTrue(body['stream'])
                self.assertEqual(body['text']['format']['type'], 'json_object')
                self.assertNotIn('temperature', body); self.assertNotIn('stream_options', body)
                self.assertEqual(body['max_output_tokens'], 512)
        self.assertEqual(len(self.ai.session(self.key, run['session'])['messages']), 2)

    def test_nonstream_none_and_output_limit(self):
        self.use_openai(stream=False, enable_thinking=False)
        run = self.start('inspect only')
        self.assertEqual(run['status'], 'success', run.get('error'))
        self.assertEqual(self.openai.requests[-1][1]['reasoning'], {'effort': 'none'})
        self.openai.incomplete = True
        for streaming in [False, True]:
            self.ai.update_config({'stream': streaming})
            limited = self.start('inspect only')
            self.assertEqual(limited['status'], 'error'); self.assertIn('출력 길이 제한', limited['error'])
            self.assertFalse(limited['proposals'])

    def test_openai_counting_and_compaction_preserve_full_transcript(self):
        self.use_openai(context_window=4096)
        session = self.ai.load_session(self.key)
        for number in range(10):
            for role in ['user', 'assistant']:
                item = {'role': role, 'content': f'Decision {number}: ' + 'Preserve the reported facts and citations. ' * 30}
                session['model_history'].append(item.copy()); session['messages'].append(dict(item, id=f'{role}-{number}'))
        history = list(session['messages']); self.ai.save_session(session)
        run = self.start('inspect only', content='Manuscript text.\n' * 3000, session=session['id'])
        self.assertEqual(run['status'], 'success', run.get('error'))
        self.assertGreater(run['compactions'], 0)
        self.assertEqual(self.ai.load_session(self.key, session['id'])['messages'][:len(history)], history)
        calls = [body for path, body in self.openai.requests if path.endswith('/responses')]
        self.assertTrue(any(body['input'][0]['content'].startswith('Summarize editorial context') for body in calls))
        for body in calls:
            self.assertLessEqual(tests.token_count(body['input']) + body['max_output_tokens'] + 256, 4096)

    def test_missing_key_environment_and_error_redaction(self):
        settings = self.ai.update_config({'provider': 'openai', 'openai_base_url': self.base})
        self.assertFalse(settings['connected']); self.assertFalse(settings['api_key_configured'])
        with self.assertRaises(tests.AssistantError):
            self.start('inspect only')
        with patch.dict(os.environ, {'OPENAI_API_KEY': KEY}):
            result = self.ai.settings(); self.assertTrue(result['connected']); self.assertEqual(result['api_key_source'], 'environment')
            self.openai.reject_key = True
            self.ai.model_cache.clear()
            rejected = self.ai.settings()
            self.assertFalse(rejected['connected']); self.assertNotIn(KEY, json.dumps(rejected)); self.assertIn('[REDACTED]', rejected['error'])
        self.openai.reject_key = False
        self.use_openai()
        cleared = self.ai.update_config({'clear_api_key': True})
        self.assertFalse(cleared['api_key_configured']); self.assertFalse(self.ai.api_key_path.exists())

    def test_openai_url_validation(self):
        for bad in ['http://untrusted.example/v1', 'https://user:secret@api.openai.com/v1', 'https://api.openai.com/v1?key=x', 'https://api.openai.com/v1#x']:
            with self.subTest(url=bad), self.assertRaises(tests.AssistantError):
                self.ai.update_config({'openai_base_url': bad})


if __name__ == '__main__':
    unittest.main()

"""EXAONE wire-format and key-isolation tests; never calls the external API."""
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('exaone_local_tests', Path(__file__).with_name('test_ai_assistant.py'))
tests = importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
KEY = 'test-exaone-not-a-real-key'
OPENAI_KEY = 'test-other-provider-not-a-real-key'
MODEL = 'k-exaone_v2.5'


class FakeExaone(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        self.server.gets.append(self.path)
        self.reply({'error': {'message': 'No model listing endpoint'}}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, body))
        self.server.auth.append(self.headers.get('Authorization'))
        if self.headers.get('Authorization') != 'Bearer ' + KEY or getattr(self.server, 'reject', False):
            self.reply({'error': {'message': 'Invalid key ' + KEY}}, 401); return
        if self.path != '/v1/chat/completions':
            self.reply({'error': {'message': 'Unsupported endpoint'}}, 404); return
        messages = body['messages']
        if messages == [{'role':'user', 'content':'Reply OK.'}]:
            self.reply({'choices':[{'message':{'content':''},'finish_reason':'length'}]}); return
        if messages[0]['content'].startswith('Summarize editorial context'):
            answer = 'Preserve facts, author constraints, citations and pending edits.'
        else:
            current = next(message for message in messages if 'CURRENT EDITOR CONTEXT:\n' in message['content'])
            context = json.loads(current['content'].split('CURRENT EDITOR CONTEXT:\n', 1)[1])
            if 'inspect only' in current['content']:
                answer = json.dumps({'action':'final','answer':'EXAONE 연결과 현재 문맥을 확인했습니다.'}, ensure_ascii=False)
            elif messages[-1]['content'].startswith('TOOL OBSERVATION:'):
                previous = json.loads(messages[-2]['content'])
                observation = json.loads(messages[-1]['content'].split('\n', 1)[1])
                if previous['action'] == 'read_file':
                    answer = json.dumps({'action':'propose_edit','arguments':{'path':context['file'],'old_text':observation['content'],'new_text':'We evaluated the model output.','explanation':'명확한 문장으로 바꿉니다.'}}, ensure_ascii=False)
                else:
                    answer = json.dumps({'action':'final','answer':'EXAONE이 수정안을 제안했습니다.'}, ensure_ascii=False)
            else:
                answer = json.dumps({'action':'read_file','arguments':{'path':context['file']}})
        finish = 'length' if getattr(self.server, 'incomplete', False) else 'stop'
        usage = {'prompt_tokens':100,'completion_tokens':30,'reasoning_tokens':12}
        if not body.get('stream'):
            self.reply({'choices':[{'message':{'content':'\n\n' + answer,'reasoning_content':'PRIVATE_REASONING'},'finish_reason':finish}], 'usage':usage}); return
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Connection','close'); self.end_headers(); self.close_connection=True
        def event(data):
            self.wfile.write(('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode()); self.wfile.flush()
        try:
            event({'choices':[{'delta':{'reasoning_content':'PRIVATE_REASONING'},'index':0}]})
            for index in range(0,len(answer),4):
                event({'choices':[{'delta':{'content':answer[index:index+4]},'index':0}]})
                time.sleep(.001)
            event({'choices':[{'delta':{},'finish_reason':finish,'index':0}]})
            event({'choices':[],'usage':usage})
            self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


class ExaoneProviderTests(unittest.TestCase):
    wait_run = tests.AssistantTests.wait_run
    start = tests.AssistantTests.start

    def setUp(self):
        tests.AssistantTests.setUp(self)
        self.env = patch.dict(os.environ, {'EXAONE_API_KEY':'', 'OPENAI_API_KEY':''}); self.env.start()
        self.exaone = ThreadingHTTPServer(('127.0.0.1',0), FakeExaone)
        self.exaone.requests, self.exaone.auth, self.exaone.gets = [], [], []
        self.exaone_thread = threading.Thread(target=self.exaone.serve_forever,daemon=True); self.exaone_thread.start()
        self.base = f'http://127.0.0.1:{self.exaone.server_port}/v1'

    def tearDown(self):
        self.exaone.shutdown(); self.exaone.server_close(); self.exaone_thread.join(); self.env.stop()
        tests.AssistantTests.tearDown(self)

    def use_exaone(self, **values):
        return self.ai.update_config({'provider':'exaone','exaone_base_url':self.base,'api_key':KEY, **values})

    def test_defaults_key_isolation_and_real_connection_check(self):
        config = self.ai.config()
        self.assertEqual(config['exaone_model'],MODEL)
        self.assertEqual(config['exaone_base_url'],'https://api.exaone.ai/v1')
        self.ai.store_api_key(OPENAI_KEY,dict(config,provider='openai'))
        settings = self.use_exaone(context_window=0)
        self.assertTrue(settings['connected'])
        self.assertEqual(settings['context_window'],524288)
        self.assertEqual(settings['active_model'],MODEL)
        self.assertEqual(self.exaone.gets,[])
        self.assertEqual(self.exaone.auth,['Bearer '+KEY])
        self.assertEqual(self.exaone.requests[0][1]['max_tokens'],16)
        for _ in range(3):
            self.assertTrue(self.ai.settings()['connected'])
        self.assertEqual(len(self.exaone.requests),1,'Settings reads must not generate paid requests')
        self.assertEqual(self.ai.api_key_path.read_text(),OPENAI_KEY)
        self.assertEqual(self.ai.exaone_api_key_path.read_text(),KEY)
        self.assertEqual(self.ai.exaone_api_key_path.stat().st_mode & 0o777,0o600)
        for key in (KEY,OPENAI_KEY):
            self.assertNotIn(key,json.dumps(settings)); self.assertNotIn(key,self.config_path.read_text())
        self.ai.update_config({'clear_api_key':True})
        self.assertFalse(self.ai.exaone_api_key_path.exists())
        self.assertEqual(self.ai.api_key_path.read_text(),OPENAI_KEY)
        local = self.ai.update_config({'provider':'local'})
        self.assertEqual(local['active_model'],'TestWriter'); self.assertTrue(local['connected'])

    def test_stream_react_native_effort_and_no_reasoning_leak(self):
        self.use_exaone(enable_thinking=False, exaone_reasoning_effort='high')
        run = self.start()
        self.assertEqual(run['status'],'success',run.get('error'))
        self.assertEqual(run['provider'],'exaone'); self.assertEqual(run['model'],MODEL)
        self.assertTrue(run['thinking_enabled']); self.assertIsNone(run['thinking_budget'])
        self.assertEqual(run['thinking_level'],'high')
        self.assertEqual(run['token_count_method'],'estimate')
        self.assertEqual(run['usage']['reasoning_tokens'],12)
        self.assertTrue(run['proposals'])
        self.assertEqual((self.project/'section.tex').read_text(),'Disk text.')
        self.assertNotIn('PRIVATE_REASONING',json.dumps(run))
        self.assertNotIn(KEY,json.dumps(run))
        for path,body in self.exaone.requests[1:]:
            self.assertEqual(path,'/v1/chat/completions')
            self.assertEqual(body['model'],MODEL)
            self.assertEqual(body['reasoning_effort'],'high')
            self.assertEqual(body['temperature'],1.0); self.assertEqual(body['top_p'],.95)
            self.assertEqual(body['stream_options'],{'include_usage':True})
            for invalid in ('thinking_token_budget','chat_template_kwargs','reasoning','response_format','store','input'):
                self.assertNotIn(invalid,body)
        for path in self.workspace.state.glob('ai/**/*.json'):
            self.assertNotIn(KEY,path.read_text())
            self.assertNotIn('PRIVATE_REASONING',path.read_text())

    def test_nonstream_usage_and_truncated_response(self):
        self.use_exaone(stream=False,exaone_reasoning_effort='low')
        run = self.start('inspect only')
        self.assertEqual(run['status'],'success',run.get('error'))
        self.assertEqual(run['answer'],'EXAONE 연결과 현재 문맥을 확인했습니다.')
        self.assertEqual(run['usage']['reasoning_tokens'],12)
        self.exaone.incomplete=True
        for streaming in (False,True):
            self.ai.update_config({'stream':streaming})
            run = self.start('inspect only')
            self.assertEqual(run['status'],'error'); self.assertIn('출력 길이 제한',run['error'])

    def test_missing_key_environment_and_redaction(self):
        settings = self.ai.update_config({'provider':'exaone','exaone_base_url':self.base})
        self.assertFalse(settings['connected']); self.assertFalse(settings['api_key_configured'])
        self.assertEqual(self.exaone.requests,[])
        with self.assertRaises(tests.AssistantError):self.start('inspect only')
        with patch.dict(os.environ,{'EXAONE_API_KEY':KEY}):
            settings = self.ai.settings(verify=True)
            self.assertTrue(settings['connected']); self.assertEqual(settings['api_key_source'],'environment')
            self.exaone.reject=True
            settings = self.ai.settings(verify=True)
            self.assertFalse(settings['connected']); self.assertNotIn(KEY,json.dumps(settings)); self.assertIn('[REDACTED]',settings['error'])
            run = self.start('inspect only')
            self.assertEqual(run['status'],'error'); self.assertNotIn(KEY,json.dumps(run))

    def test_compaction_and_server_restart(self):
        self.use_exaone(context_window=4096)
        session = self.ai.load_session(self.key)
        for number in range(8):
            for role in ('user','assistant'):
                message={'role':role,'content':f'Turn {number}. '+ 'Preserve facts and pending edits. '*20}
                session['model_history'].append(message.copy());session['messages'].append(dict(message,id=f'{role}-{number}'))
        self.ai.save_session(session)
        run=self.start('inspect only',content='Manuscript text.\n'*1000,session=session['id'])
        self.assertEqual(run['status'],'success',run.get('error')); self.assertGreater(run['compactions'],0)
        restarted=tests.AIAssistant(self.workspace,self.config_path,self.prompt_path)
        settings=restarted.settings()
        self.assertTrue(settings['api_key_configured']);self.assertFalse(settings['connected'])
        self.assertTrue(restarted.settings(verify=True)['connected'])

    def test_reject_unsupported_effort_and_url(self):
        for values in ({'exaone_reasoning_effort':'medium'}, {'exaone_reasoning_effort':'none'}, {'exaone_model':'K-EXAONE-2.0'}, {'exaone_base_url':'https://other.example/v1'}, {'exaone_base_url':'https://user:pass@api.exaone.ai/v1'}, {'exaone_base_url':'https://api.exaone.ai/v1?key=x'}):
            with self.subTest(values=values), self.assertRaises(tests.AssistantError):
                self.ai.update_config(values)


if __name__ == '__main__':
    unittest.main()

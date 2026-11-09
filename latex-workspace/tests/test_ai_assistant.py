import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('workspace_tests', Path(__file__).with_name('test_workspace.py'))
workspace_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace_tests)
server = workspace_tests.server
from ai_assistant import AIAssistant, AssistantError, persist, partial_final_answer, sse_data


def token_count(messages):
    return sum(len(message['content'].encode()) // 3 + 12 for message in messages) + 10


class FakeModel(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, result):
        data = json.dumps(result).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        self.reply({'data': [{'id': 'TestWriter', 'max_model_len': 4096}]})

    def stream_reply(self, result, thinking=False):
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Connection', 'close'); self.end_headers()
        self.close_connection = True
        try:
            if thinking:
                self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"FAKE_THINKING_EVENT"},"finish_reason":null}]}\n\n'); self.wfile.flush()
            try:
                is_final = json.loads(result).get('action') == 'final'
            except ValueError:
                is_final = False
            for offset in range(0, len(result), 4):
                chunk = {'choices': [{'index': 0, 'delta': {'content': result[offset:offset + 4]}, 'finish_reason': None}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode()); self.wfile.flush()
                if offset >= len(result) // 2:
                    if getattr(self.server, 'abort_stream', False):
                        return
                    if is_final and getattr(self.server, 'pause_stream', False) and not self.server.partial_ready.is_set():
                        self.server.partial_ready.set()
                        self.server.release_partial.wait(10)
                time.sleep(getattr(self.server, 'stream_delay', .001))
            self.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
            self.wfile.write(b'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":20}}\n\n')
            self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/tokenize':
            if not hasattr(self.server, 'tokenize_calls'):
                self.server.tokenize_calls = []
            self.server.tokenize_calls.append(body)
            self.reply({'count': token_count(body['messages'])}); return
        self.server.calls.append(body)
        messages = body['messages']
        if messages[0]['content'].startswith('Summarize editorial context'):
            result = 'The author wants clear prose. Preserve technical facts, LaTeX commands and citations. Pending requests remain open.'
        else:
            current = next(item for item in messages if 'CURRENT EDITOR CONTEXT:\n' in item['content'])
            context = json.loads(current['content'].split('CURRENT EDITOR CONTEXT:\n', 1)[1])
            if 'inspect only' in current['content']:
                result = json.dumps({'action': 'final', 'answer': '현재 파일 문맥을 확인했습니다.'})
            elif messages[-1]['content'].startswith('TOOL OBSERVATION:'):
                previous = json.loads(messages[-2]['content'])
                observation = json.loads(messages[-1]['content'].split('\n', 1)[1])
                if previous['action'] == 'read_file':
                    result = json.dumps({'action': 'propose_edit', 'arguments': {'path': context['file'], 'old_text': observation['content'], 'new_text': 'We evaluated the model output.', 'explanation': '주체와 동사를 가깝게 두었습니다.'}})
                else:
                    result = json.dumps({'action': 'final', 'answer': '검토할 수정안 한 개를 만들었습니다.'})
            else:
                result = json.dumps({'action': 'read_file', 'arguments': {'path': context['file']}})
        if body.get('stream'):
            self.stream_reply(result, body.get('chat_template_kwargs', {}).get('enable_thinking', False))
        else:
            self.reply({'choices': [{'message': {'content': result}, 'finish_reason': 'stop'}]})


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / 'paper'; self.project.mkdir()
        (self.project / 'main.tex').write_text('\\documentclass{article}\n\\begin{document}Disk text.\\end{document}')
        (self.project / 'section.tex').write_text('Disk text.')
        self.workspace = server.Workspace(self.root)
        self.key = self.workspace.discover()[0]['id']
        self.model = ThreadingHTTPServer(('127.0.0.1', 0), FakeModel); self.model.calls = []
        self.thread = threading.Thread(target=self.model.serve_forever, daemon=True); self.thread.start()
        self.config_path = self.root / 'config.json'; self.prompt_path = self.root / 'prompt.txt'
        self.prompt_path.write_text('Be a clear scientific editor. Preserve citations and facts.')
        persist(self.config_path, {'port': self.model.server_port, 'model': 'TestWriter', 'context_window': 4096, 'max_output_tokens': 512, 'summary_tokens': 256, 'compaction_threshold': .65})
        self.ai = AIAssistant(self.workspace, self.config_path, self.prompt_path)
        self.workspace.ai = self.ai

    def tearDown(self):
        self.model.shutdown(); self.model.server_close(); self.thread.join(); self.temporary.cleanup()

    def wait_run(self, run):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.ai.get_run(run['id'])
            if result['status'] != 'running':
                return result
            time.sleep(.02)
        self.fail('AI run timed out')

    def start(self, message='correct this', content='The model output was evaluated by us.', session=None):
        return self.wait_run(self.ai.start({'project': self.key, 'session': session, 'message': message,
                    'context': {'path': 'section.tex', 'content': content, 'cursor_line': 1, 'selection': {'text': content, 'start_line': 1, 'end_line': 1}}}))

    def test_react_reads_unsaved_buffer_and_proposes_without_writing(self):
        run = self.start()
        self.assertEqual(run['status'], 'success', run.get('error'))
        self.assertEqual([event['action'] for event in run['events']], ['read_file', 'propose_edit'])
        self.assertEqual(run['proposals'][0]['old_text'], 'The model output was evaluated by us.')
        self.assertEqual((self.project / 'section.tex').read_text(), 'Disk text.')
        self.assertIn('CURRENT EDITOR CONTEXT', self.model.calls[0]['messages'][-1]['content'])
        self.assertEqual(len(self.ai.session(self.key, run['session'])['messages']), 2)
        self.ai.proposal_status({'project': self.key, 'session': run['session'], 'proposal': run['proposals'][0]['id'], 'status': 'dismissed'})
        self.assertEqual(self.ai.session(self.key, run['session'])['messages'][-1]['proposals'][0]['status'], 'dismissed')

    def test_compaction_preserves_transcript_and_never_overflows_model(self):
        session = self.ai.load_session(self.key)
        for number in range(12):
            for role in ['user', 'assistant']:
                message = {'role': role, 'content': f'Turn {number}. ' + 'Preserve scientific meaning and the reported numbers. ' * 20}
                session['model_history'].append(message.copy()); session['messages'].append(dict(message, id=f'{role}-{number}'))
        transcript = list(session['messages'])
        self.ai.save_session(session)
        run = self.start('inspect only', content='Line of manuscript.\n' * 5000, session=session['id'])
        self.assertEqual(run['status'], 'success', run.get('error'))
        self.assertGreater(run['compactions'], 0)
        self.assertTrue(run['context_truncated'])
        saved = self.ai.load_session(self.key, session['id'])
        self.assertTrue(saved['summary'])
        self.assertEqual(saved['messages'][:len(transcript)], transcript)
        self.assertLess(len(saved['model_history']), len(session['model_history']))
        self.assertTrue(any(call['messages'][0]['content'].startswith('Summarize editorial context') for call in self.model.calls))
        for call in self.model.calls:
            self.assertLessEqual(token_count(call['messages']) + call['max_tokens'] + 256, 4096)

    def test_prompt_reload_and_context_refresh_on_every_turn(self):
        first = self.start('inspect only', 'First file buffer.')
        self.prompt_path.write_text('EDITED PROMPT: use concise prose.')
        second = self.start('inspect only', 'Changed current buffer.', first['session'])
        self.assertEqual(second['status'], 'success', second.get('error'))
        self.assertIn('EDITED PROMPT', self.model.calls[-1]['messages'][0]['content'])
        self.assertIn('Changed current buffer.', self.model.calls[-1]['messages'][-1]['content'])
        self.assertNotIn('First file buffer.', self.model.calls[-1]['messages'][-1]['content'])

    def test_tools_reject_outside_paths_and_ambiguous_edits(self):
        run = {'project': self.key, 'events': [], 'proposals': []}
        with self.assertRaises(server.APIError):
            self.ai.tool('read_file', {'path': '../outside.tex'}, run, {})
        context = {'path': 'section.tex', 'content': 'repeat repeat'}
        observation = self.ai.tool('propose_edit', {'path': 'section.tex', 'old_text': 'repeat', 'new_text': 'change'}, run, context)
        self.assertIn('error', observation); self.assertFalse(run['proposals'])

    def test_settings_persist_and_model_limit_is_verified(self):
        settings = self.ai.update_config({'context_window': 8192, 'max_output_tokens': 512})
        self.assertTrue(settings['connected'])
        self.assertEqual(settings['context_window'], 4096)
        self.assertEqual(json.loads(self.config_path.read_text())['context_window'], 8192)
        with self.assertRaises(AssistantError):
            self.ai.update_config({'port': 70000})

    def test_final_answer_is_visible_before_completion_and_saved_once(self):
        self.model.pause_stream = True; self.model.partial_ready = threading.Event(); self.model.release_partial = threading.Event()
        self.model.stream_delay = .004
        run = self.ai.start({'project': self.key, 'message': 'inspect only', 'context': {'path': 'section.tex', 'content': 'Current buffer.'}})
        try:
            self.assertTrue(self.model.partial_ready.wait(5))
            progress = self.ai.get_run(run['id'])
            self.assertEqual(progress['status'], 'running')
            self.assertTrue(progress['partial_answer'])
            self.assertTrue('현재 파일 문맥을 확인했습니다.'.startswith(progress['partial_answer']))
            self.assertNotIn('"action"', progress['partial_answer'])
            self.assertEqual(len(self.ai.session(self.key, run['session'])['messages']), 1)
        finally:
            self.model.release_partial.set()
        done = self.wait_run(run)
        self.assertEqual(done['status'], 'success', done.get('error'))
        self.assertEqual(done['answer'], '현재 파일 문맥을 확인했습니다.')
        self.assertEqual(done['partial_answer'], '')
        self.assertEqual(len(self.ai.session(self.key, run['session'])['messages']), 2)
        self.assertTrue(all(call['stream'] and call['stream_options']['include_usage'] for call in self.model.calls))

    def test_cancellation_interrupts_a_stalled_stream(self):
        self.model.pause_stream = True; self.model.partial_ready = threading.Event(); self.model.release_partial = threading.Event()
        run = self.ai.start({'project': self.key, 'message': 'inspect only', 'context': {'path': 'section.tex', 'content': 'Current buffer.'}})
        try:
            self.assertTrue(self.model.partial_ready.wait(5))
            started = time.monotonic(); self.ai.cancel(run['id'])
            done = self.wait_run(run)
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(done['status'], 'cancelled')
            self.assertEqual(done['partial_answer'], '')
            self.assertFalse(done['proposals'])
        finally:
            self.model.release_partial.set()

    def test_interrupted_generation_does_not_execute_a_partial_tool(self):
        self.model.abort_stream = True
        run = self.start()
        self.assertEqual(run['status'], 'error')
        self.assertIn('스트림 연결', run['error'])
        self.assertFalse(run['proposals'])
        self.assertEqual((self.project / 'section.tex').read_text(), 'Disk text.')

    def test_nonstreaming_setting_still_works(self):
        self.ai.update_config({'stream': False})
        done = self.start()
        self.assertEqual(done['status'], 'success', done.get('error'))
        self.assertTrue(done['proposals'])
        self.assertTrue(all(call['stream'] is False and 'stream_options' not in call for call in self.model.calls))

    def test_thinking_levels_change_real_request_budgets(self):
        for level, budget in [('low', 256), ('medium', 1024), ('high', 1536)]:
            with self.subTest(level=level):
                settings = self.ai.update_config({'enable_thinking': True, 'thinking_level': level, 'max_output_tokens': 2048})
                self.assertEqual(settings['thinking']['budget'], budget)
                done = self.start('inspect only')
                self.assertEqual(done['status'], 'success', done.get('error'))
                self.assertTrue(done['thinking_enabled'])
                self.assertEqual(done['thinking_budget'], budget)
                request = self.model.calls[-1]
                self.assertTrue(request['chat_template_kwargs']['enable_thinking'])
                self.assertEqual(request['thinking_token_budget'], budget)
                self.assertEqual(request['max_tokens'], 2048)
                self.assertEqual(self.model.tokenize_calls[-1]['chat_template_kwargs'], request['chat_template_kwargs'])
                self.assertNotIn('FAKE_THINKING_EVENT', done['answer'])
        self.ai.update_config({'enable_thinking': False})
        off = self.start('inspect only')
        self.assertEqual(off['thinking_budget'], 0)
        self.assertFalse(self.model.calls[-1]['chat_template_kwargs']['enable_thinking'])
        self.assertNotIn('thinking_token_budget', self.model.calls[-1])

    def test_custom_thinking_budget_validation_and_short_summary_reserve(self):
        self.ai.update_config({'enable_thinking': True, 'thinking_level': 'custom', 'thinking_token_budget': 300, 'max_output_tokens': 512})
        done = self.start('inspect only')
        self.assertEqual(done['status'], 'success', done.get('error'))
        self.assertEqual(self.model.calls[-1]['thinking_token_budget'], 300)
        config = self.ai.config()
        self.assertEqual(self.ai.thinking_budget(config, output_tokens=256), 128)
        self.assertEqual(self.ai.thinking_budget(config, output_tokens=128), 0)
        for invalid in [{'thinking_token_budget': 385}, {'thinking_token_budget': 0}, {'thinking_token_budget': 1.5},
                        {'thinking_level': 'unknown'}, {'max_output_tokens': 128}, {'enable_thinking': 'false'}]:
            with self.subTest(invalid=invalid), self.assertRaises(AssistantError):
                self.ai.update_config(invalid)
        self.assertEqual(self.ai.config()['thinking_token_budget'], 300)

    def test_existing_template_switch_still_enables_thinking(self):
        config = json.loads(self.config_path.read_text())
        config['chat_template_kwargs'] = {'enable_thinking': True, 'preserve_thinking': False}
        persist(self.config_path, config)
        self.assertTrue(self.ai.settings()['thinking']['enabled'])
        done = self.start('inspect only')
        self.assertEqual(done['status'], 'success', done.get('error'))
        self.assertEqual(self.model.calls[-1]['thinking_token_budget'], 256)
        self.ai.update_config({'enable_thinking': False})
        self.assertFalse(self.ai.config()['chat_template_kwargs']['preserve_thinking'])


class StreamParsingTests(unittest.TestCase):
    def test_partial_json_handles_korean_newlines_latex_quotes_and_surrogates(self):
        answer = '한글\n수식 \\alpha와 "따옴표" 😀'
        encoded = json.dumps({'action': 'final', 'answer': answer}, ensure_ascii=True)
        for end in range(len(encoded) + 1):
            partial = partial_final_answer(encoded[:end])
            self.assertTrue(answer.startswith(partial), repr(partial))
            partial.encode('utf-8')
        self.assertEqual(partial_final_answer(encoded), answer)

    def test_tool_arguments_and_unknown_actions_never_render_as_answer(self):
        self.assertEqual(partial_final_answer('{"action":"read_file","arguments":{"answer":"hidden"}}'), '')
        self.assertEqual(partial_final_answer('{"answer":"hidden"'), '')
        self.assertEqual(partial_final_answer('{"answer":"visible","action":"final"}'), 'visible')

    def test_sse_comments_multiline_data_and_crlf(self):
        source = io.BytesIO(b': keep-alive\r\n\r\ndata: first\r\ndata: second\r\n\r\ndata: [DONE]\n\n')
        self.assertEqual(list(sse_data(source)), ['first\nsecond', '[DONE]'])


if __name__ == '__main__':
    unittest.main()

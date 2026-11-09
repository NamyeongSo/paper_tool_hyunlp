"""Run: python -m unittest discover -s latex-workspace/tests -v"""
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

spec = importlib.util.spec_from_file_location('folio_server', Path(__file__).resolve().parents[1] / 'server.py')
server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = server
spec.loader.exec_module(server)

MAIN = r'''\documentclass{article}
\title{Test Paper}
\begin{document}
\maketitle
\input{sections/introduction}
\end{document}
'''


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'paper'
        (self.project / 'sections').mkdir(parents=True)
        (self.project / 'main.tex').write_text(MAIN)
        (self.project / 'sections/introduction.tex').write_text('Hello from the paper.\n')
        self.workspace = server.Workspace(self.root)
        self.key = self.workspace.discover()[0]['id']

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovers_real_main_document_and_nested_files(self):
        info = self.workspace.project_info(self.key)
        self.assertEqual(info['main'], 'main.tex')
        self.assertEqual(info['title'], 'Test Paper')
        tree = self.workspace.tree(self.key)
        self.assertEqual(tree[0]['children'][0]['path'], 'sections/introduction.tex')

    def test_blocks_traversal_and_external_symlinks(self):
        (self.root / 'secret.tex').write_text('external')
        (self.project / 'linked.tex').symlink_to(self.root / 'secret.tex')
        for path in ['../secret.tex', '/etc/passwd', 'linked.tex', '.hidden.tex']:
            with self.subTest(path=path), self.assertRaises(server.APIError):
                self.workspace.read_file(self.key, path)

    def test_saves_disk_and_original_backup(self):
        original = self.workspace.read_file(self.key, 'main.tex')
        result = self.workspace.save_file(dict(original, project=self.key, content=MAIN + '% edit\n'))
        self.assertNotEqual(original['revision'], result['revision'])
        self.assertTrue((self.project / 'main.tex').read_text().endswith('% edit\n'))
        backups = list((self.workspace.state / 'backups' / self.key / 'main.tex').glob('*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), MAIN)

    def test_conflicting_save_does_not_overwrite_external_change(self):
        original = self.workspace.read_file(self.key, 'main.tex')
        (self.project / 'main.tex').write_text('External update')
        with self.assertRaises(server.APIError) as raised:
            self.workspace.save_file(dict(original, project=self.key, content='Stale browser text'))
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual((self.project / 'main.tex').read_text(), 'External update')

    def test_creates_nested_file_and_rejects_duplicate(self):
        data = {'project': self.key, 'path': 'new/section.tex', 'kind': 'file'}
        self.workspace.create_entry(data)
        self.assertTrue((self.project / 'new/section.tex').is_file())
        with self.assertRaises(server.APIError):
            self.workspace.create_entry(data)

    def wait_job(self, job):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = self.workspace.job_status(job['id'])
            if result['status'] != 'running' and self.key not in self.workspace.active:
                return result
            time.sleep(.05)
        self.fail('Compile did not finish in 30 seconds')

    @unittest.skipUnless(server.shutil.which('latexmk') and server.shutil.which('pdflatex'), 'TeX Live required')
    def test_real_compile_and_error_preserves_previous_pdf(self):
        good = self.wait_job(self.workspace.start_compile({'project': self.key, 'main': 'main.tex'}))
        self.assertEqual(good['status'], 'success', good['log'][-2000:])
        pdf = self.workspace.pdf(self.key).read_bytes()
        self.assertTrue(pdf.startswith(b'%PDF-'))
        (self.project / 'sections/introduction.tex').write_text('\\undefinedFolioCommand\n')
        bad = self.wait_job(self.workspace.start_compile({'project': self.key, 'main': 'main.tex'}))
        self.assertEqual(bad['status'], 'error')
        self.assertTrue(any(e['file'] == 'sections/introduction.tex' and e['line'] == 1 for e in bad['errors']))
        self.assertEqual(self.workspace.pdf(self.key).read_bytes(), pdf)

    @unittest.skipUnless(all(server.shutil.which(command) for command in ('latexmk', 'pdflatex', 'synctex', 'pdftotext')), 'TeX Live and Poppler required')
    def test_pdf_coordinates_resolve_source_and_survive_snapshot_cleanup(self):
        section = self.project / 'sections/introduction.tex'
        section.write_text('% A comment\n\n\\section{Introduction}\n\nHello from the paper.\n')
        good = self.wait_job(self.workspace.start_compile({'project': self.key, 'main': 'main.tex'}))
        self.assertEqual(good['status'], 'success', good['log'][-2000:])
        version = good['pdf_version']
        pdf = self.workspace.pdf(self.key, version)
        xml = server.subprocess.run(['pdftotext', '-bbox', str(pdf), '-'], capture_output=True, text=True, check=True).stdout
        word = next(element for element in ET.fromstring(xml).iter() if element.tag.endswith('}word') and element.text == 'Hello')
        x = (float(word.attrib['xMin']) + float(word.attrib['xMax'])) / 2
        y = (float(word.attrib['yMin']) + float(word.attrib['yMax'])) / 2
        location = self.workspace.source_location(self.key, version, 1, x, y, 'Hello from the paper.')
        self.assertEqual((location['path'], location['line']), ('sections/introduction.tex', 5))
        self.assertFalse(location['source_changed'])
        self.assertTrue(pdf.with_suffix('.synctex.gz').is_file())
        self.assertTrue(pdf.with_suffix('.sources.json').is_file())
        # A PDF stays linked even after the separate build directory is cleaned up.
        server.shutil.rmtree(self.workspace.state / 'projects' / self.key / 'jobs' / version)
        section.write_text('% inserted\n% inserted\n% inserted\n' + section.read_text())
        restarted = server.Workspace(self.root)
        moved = restarted.source_location(self.key, version, 1, x, y, 'Hello from the paper.')
        self.assertEqual((moved['path'], moved['line'], moved['compiled_line']), ('sections/introduction.tex', 8, 5))
        self.assertTrue(moved['source_changed'])
        for page, px, py in [(0, x, y), ('bad', x, y), (1, 'nan', y), (1, x, 'inf'), (1, -1, y)]:
            with self.subTest(page=page, x=px, y=py), self.assertRaises(server.APIError) as error:
                restarted.source_location(self.key, version, page, px, py)
            self.assertEqual(error.exception.status, 400)
        original_pdf = pdf.read_bytes()
        section.write_text(section.read_text().replace('Hello', 'Goodbye'))
        self.workspace = restarted
        newer = self.wait_job(restarted.start_compile({'project': self.key, 'main': 'main.tex'}))
        self.assertEqual(newer['status'], 'success')
        self.assertEqual(restarted.pdf(self.key, version).read_bytes(), original_pdf)
        self.assertNotEqual(restarted.pdf(self.key).read_bytes(), original_pdf)

    def test_rejects_invalid_pdf_version(self):
        for version in ['../outside', '../outside.pdf', 'not-a-version']:
            with self.subTest(version=version), self.assertRaises(server.APIError) as raised:
                self.workspace.pdf(self.key, version)
            self.assertEqual(raised.exception.status, 400)

    def test_http_rejects_cross_origin_writes(self):
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        http.workspace = self.workspace
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        try:
            url = f'http://127.0.0.1:{http.server_port}'
            self.assertEqual(urlopen(url + '/').status, 200)
            request = Request(url + '/api/entry', data=json.dumps({'project': self.key, 'path': 'bad.tex'}).encode(),
                              headers={'Content-Type': 'application/json', 'Origin': 'https://unrelated.example'})
            with self.assertRaises(HTTPError) as raised:
                urlopen(request)
            self.assertEqual(raised.exception.code, 403)
            self.assertFalse((self.project / 'bad.tex').exists())
        finally:
            http.shutdown(); http.server_close(); worker.join()


if __name__ == '__main__':
    unittest.main()

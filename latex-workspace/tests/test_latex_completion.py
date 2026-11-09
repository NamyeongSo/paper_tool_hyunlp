"""Bibliography/label parsing, project boundaries, and unsaved completion buffers."""
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from urllib.request import Request, urlopen

spec=importlib.util.spec_from_file_location('completion_workspace_tests',Path(__file__).with_name('test_workspace.py'))
tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)
from latex_symbols import bibliography, tex_symbols

BIB=r'''% @article{fake, title={Ignore this}}
@string{conf = "Conference"}
@article{Smith2024,
  title = {A {Nested} Title for {LLMs}},
  author = "Smith, Jane and Kim, Mina",
  year = 2024,
  journal = conf # " Proceedings"
}
@comment{ @article{hidden, title={Ignore nested entry}} }
@preamble{"Ignore this too"}
@inproceedings(lee2025,
 title = "A ) delimiter inside a quoted title",
 author = {Lee, Author}, date = {2025-03}
)
'''


class CompletionTests(unittest.TestCase):
    setUp=tests.WorkspaceTests.setUp
    tearDown=tests.WorkspaceTests.tearDown

    def test_bibliography_nested_braces_strings_and_parentheses(self):
        items=bibliography(BIB,'references.bib')
        self.assertEqual([item['key'] for item in items],['Smith2024','lee2025'])
        self.assertEqual(items[0]['title'],'A Nested Title for LLMs')
        self.assertIn('Jane',items[0]['author'])
        self.assertEqual(items[0]['year'],'2024')
        self.assertEqual(items[1]['title'],'A ) delimiter inside a quoted title')
        self.assertEqual(items[1]['year'],'2025-03')
        self.assertEqual(items[0]['line'],3)

    def test_labels_bibitems_ignore_comments_and_verbatim(self):
        content=r'''\section{Methods and {Details}}
% \label{commented}
\label{sec:method}
\begin{verbatim}
\label{example-only}
\end{verbatim}
Escaped percent \% is text. \label{sec:percent}
\verb|\label{inline-example}|
\bibitem[Smith(2024)]{inline2024} An inline reference.
'''
        items=tex_symbols(content,'section.tex')
        self.assertEqual([item['key'] for item in items],['sec:method','sec:percent','inline2024'])
        self.assertEqual(items[0]['title'],'Methods and Details')
        self.assertEqual(items[0]['line'],3)
        self.assertEqual(items[-1]['kind'],'citation')

    def test_index_nested_files_and_unsaved_buffers(self):
        (self.project/'refs.bib').write_text(BIB)
        path=self.project/'sections/introduction.tex'
        path.write_text('\\section{Existing}\n\\label{sec:old}\n')
        original=path.read_text()
        result=self.workspace.completions.collect(self.key,{'sections/introduction.tex':original+'\\label{sec:unsaved}\n'})
        keys={item['key'] for item in result['items']}
        self.assertTrue({'Smith2024','lee2025','sec:old','sec:unsaved'}<=keys)
        self.assertEqual(path.read_text(),original)
        self.assertNotIn('sec:unsaved',{item['key'] for item in self.workspace.completions.collect(self.key)['items']})

    def test_cache_detects_edited_and_deleted_files(self):
        path=self.project/'refs.bib';path.write_text('@misc{old, title={Old title}}')
        self.assertIn('old',{item['key'] for item in self.workspace.completions.collect(self.key)['items']})
        path.write_text('@misc{new, title={New title}}')
        keys={item['key'] for item in self.workspace.completions.collect(self.key)['items']}
        self.assertIn('new',keys);self.assertNotIn('old',keys)
        path.unlink()
        self.assertNotIn('new',{item['key'] for item in self.workspace.completions.collect(self.key)['items']})

    def test_project_isolation_symlinks_and_hidden_data(self):
        (self.root/'secret.bib').write_text('@misc{secret, title={Outside project}}')
        (self.project/'link.bib').symlink_to(self.root/'secret.bib')
        hidden=self.project/'.private';hidden.mkdir();(hidden/'hidden.tex').write_text('\\label{hidden}')
        other=self.root/'second';other.mkdir();(other/'main.tex').write_text('\\documentclass{article}\n\\label{other-project}')
        self.workspace.discover()
        self.assertEqual(self.workspace.completions.collect(self.key)['items'],[])
        for buffers in ({'../secret.bib':'x'},{'link.bib':'x'},{'main.tex':None},[],{'main.tex':'x'*(4*1024*1024+1)}):
            with self.subTest(buffers=type(buffers).__name__),self.assertRaises(tests.server.APIError):
                self.workspace.completions.collect(self.key,buffers)

    def test_duplicate_keys_have_one_candidate_and_locations(self):
        (self.project/'a.bib').write_text('@misc{same, title={One}}')
        (self.project/'b.bib').write_text('@misc{same, title={Two}}')
        items=self.workspace.completions.collect(self.key)['items']
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['also_defined'],[{'path':'b.bib','line':1}])

    def test_http_completion_request_is_local_read_only(self):
        app=tests.server.ThreadingHTTPServer(('127.0.0.1',0),tests.server.Handler);app.workspace=self.workspace
        thread=threading.Thread(target=app.serve_forever,daemon=True);thread.start()
        try:
            payload={'project':self.key,'buffers':{'main.tex':tests.MAIN+'\n\\label{http-unsaved}'}}
            request=Request(f'http://127.0.0.1:{app.server_port}/api/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
            result=json.load(urlopen(request))
            self.assertIn('http-unsaved',{item['key'] for item in result['items']})
            self.assertNotIn('http-unsaved',(self.project/'main.tex').read_text())
        finally:app.shutdown();app.server_close();thread.join()


if __name__=='__main__':unittest.main()

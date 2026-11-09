"""Real SyncTeX forward navigation and persistent sentence-comment integration."""
import importlib.util
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

spec=importlib.util.spec_from_file_location('annotation_workspace_tests',Path(__file__).with_name('test_workspace.py'))
tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)
server=tests.server
from sentence_comments import offset_position


class AnnotationTests(unittest.TestCase):
    setUp=tests.WorkspaceTests.setUp
    tearDown=tests.WorkspaceTests.tearDown
    wait_job=tests.WorkspaceTests.wait_job

    def add(self,quote='Hello from the paper.',text='이 주장의 근거를 확인해주세요.',content=None):
        path='sections/introduction.tex'
        file=self.workspace.read_file(self.key,path)
        source=file['content'] if content is None else content
        at=source.index(quote)
        return self.workspace.comments.mutate({'project':self.key,'path':path,'source_revision':file['revision'],
            'from':offset_position(source,at),'to':offset_position(source,at+len(quote)), 'quote':quote,'text':text},'POST')['comments'][0]

    def test_comments_persist_move_and_do_not_change_manuscript(self):
        original=(self.project/'sections/introduction.tex').read_text()
        item=self.add()
        self.assertEqual(item['anchor']['from'],{'line':0,'ch':0})
        self.assertEqual(original,(self.project/'sections/introduction.tex').read_text())
        (self.project/'sections/introduction.tex').write_text('% new\n\n'+original)
        restarted=server.Workspace(self.root)
        moved=restarted.comments.list(self.key)[0]
        self.assertEqual(moved['id'],item['id']);self.assertEqual(moved['anchor']['from']['line'],2)
        (self.project/'sections/introduction.tex').write_text('An entirely changed sentence.')
        changed=restarted.comments.list(self.key)[0]
        self.assertIsNone(changed['anchor']);self.assertEqual(changed['anchor_state'],'changed')
        self.assertEqual(changed['quote'],'Hello from the paper.')
        self.assertEqual(changed['text'],item['text'])
        (self.project/'sections/introduction.tex').unlink()
        self.assertEqual(restarted.comments.list(self.key)[0]['anchor_state'],'missing')

    def test_comment_edit_resolve_delete_and_stale_revision(self):
        item=self.add()
        payload={'project':self.key,'id':item['id'],'revision':item['revision'],'text':'더 명확히 설명해주세요.','resolved':True}
        updated=self.workspace.comments.mutate(payload,'PUT')['comments'][0]
        self.assertEqual(updated['text'],payload['text']);self.assertTrue(updated['resolved'])
        with self.assertRaises(server.APIError) as error:self.workspace.comments.mutate(payload,'PUT')
        self.assertEqual(error.exception.status,409)
        payload.update(revision=updated['revision'])
        self.assertEqual(self.workspace.comments.mutate(payload,'DELETE')['comments'],[])

    def test_unicode_selection_and_duplicate_sentences(self):
        source='앞 문장 😀. 반복되는 문장입니다.\n다른 위치. 반복되는 문장입니다.\n'
        path=self.project/'sections/introduction.tex';path.write_text(source)
        item=self.add(quote='반복되는 문장입니다.')
        self.assertEqual(item['anchor']['from']['ch'],len('앞 문장 😀. '.encode('utf-16-le'))//2)
        path.write_text('삽입한 문장\n'+source)
        self.assertEqual(self.workspace.comments.list(self.key)[0]['anchor']['from']['line'],1)
        path.write_text('반복되는 문장입니다.\n반복되는 문장입니다.')
        self.assertIsNone(self.workspace.comments.list(self.key)[0]['anchor'])

    def test_validation_and_project_isolation(self):
        item=self.add()
        other=self.root/'another';other.mkdir();(other/'main.tex').write_text(tests.MAIN)
        key=next(project['id'] for project in self.workspace.discover() if project['name']=='another')
        self.assertEqual(self.workspace.comments.list(key),[])
        with self.assertRaises(server.APIError):self.workspace.comments.mutate({'project':key,'id':item['id'],'revision':item['revision']},'DELETE')
        file=self.workspace.read_file(self.key,'sections/introduction.tex')
        payload={'project':self.key,'path':file['path'],'source_revision':file['revision'],'from':{'line':0,'ch':0},'to':{'line':0,'ch':5},'quote':'Hello','text':'note'}
        for changes in ({'source_revision':'old'}, {'quote':'wrong'}, {'path':'../secret.tex'}, {'from':{'line':-1,'ch':0}}, {'to':{'line':0,'ch':999}}, {'text':' '}, {'text':'a'*6001}):
            with self.subTest(changes=changes),self.assertRaises(server.APIError):
                self.workspace.comments.mutate(dict(payload,**changes),'POST')

    @unittest.skipUnless(all(server.shutil.which(x) for x in ('latexmk','pdflatex','synctex')),'TeX Live required')
    def test_forward_location_exact_build_after_insertion_and_cleanup(self):
        path=self.project/'sections/introduction.tex'
        content='\\section{Introduction}\nFirst sentence at the start.\n\n\\newpage\nTarget sentence on the second page.\n\n'
        path.write_text(content)
        job=self.wait_job(self.workspace.start_compile({'project':self.key,'main':'main.tex'}))
        self.assertEqual(job['status'],'success',job['log'][-1000:])
        version=job['pdf_version'];pdf=self.workspace.pdf(self.key,version)
        # Both saved edits and unsaved browser content must use the displayed build.
        browser_content='% inserted\n% inserted\n'+content
        server.shutil.rmtree(self.workspace.state/'projects'/self.key/'jobs'/version)
        with patch.dict(os.environ,{'SYNCTEX_VIEWER':'touch /tmp/should-never-run-folio'}):
            result=self.workspace.pdf_location({'project':self.key,'path':'sections/introduction.tex','version':version,'line':7,'column':8,'content':browser_content})
        self.assertEqual(result['page'],2);self.assertEqual(result['compiled_line'],5)
        self.assertEqual(result['version'],version);self.assertTrue(result['source_changed'])
        self.assertGreater(result['box']['width'],0);self.assertGreater(result['box']['height'],0)
        x,y=result['x'],result['y']
        reverse=self.workspace.source_location(self.key,version,2,x,y,'Target sentence on the second page.')
        self.assertEqual(reverse['path'],'sections/introduction.tex')
        for content,line in [(browser_content.replace('Target sentence','New uncompiled claim'),7),('% inserted\n'+content,1)]:
            with self.assertRaises(server.APIError):
                self.workspace.pdf_location({'project':self.key,'path':'sections/introduction.tex','version':version,'line':line,'content':content})
        for value in (0,-1,True,'5',float('inf')):
            with self.subTest(line=value),self.assertRaises(server.APIError):
                self.workspace.pdf_location({'project':self.key,'path':'sections/introduction.tex','version':version,'line':value})

    def test_comment_http_routes(self):
        app=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);app.workspace=self.workspace
        thread=threading.Thread(target=app.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{app.server_port}'
        try:
            item=self.add()
            data=json.load(urlopen(base+'/api/comments?project='+self.key))
            self.assertEqual(data['comments'][0]['id'],item['id'])
            request=Request(base+'/api/comments',method='DELETE',data=json.dumps({'project':self.key,'id':item['id'],'revision':item['revision']}).encode(),headers={'Content-Type':'application/json','Origin':'https://other.example'})
            with self.assertRaises(HTTPError) as error:urlopen(request)
            self.assertEqual(error.exception.code,403)
            request.remove_header('Origin')
            self.assertEqual(json.load(urlopen(request))['comments'],[])
        finally:app.shutdown();app.server_close();thread.join()


if __name__=='__main__':unittest.main()

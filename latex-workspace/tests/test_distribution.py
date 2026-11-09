"""A clean clone must work without a personal AI configuration file."""
import importlib.util
import json
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('distribution_workspace_tests',Path(__file__).with_name('test_workspace.py'))
tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)


class DistributionTests(unittest.TestCase):
    setUp=tests.WorkspaceTests.setUp
    tearDown=tests.WorkspaceTests.tearDown

    def test_first_run_creates_config_from_nonsecret_example(self):
        path=self.root/'personal-config.json'
        assistant=tests.server.AIAssistant(self.workspace,config_path=path)
        self.assertFalse(path.exists())
        config=assistant.config()
        self.assertTrue(path.exists())
        self.assertEqual(config['provider'],'exaone')
        self.assertEqual(config['exaone_model'],'k-exaone_v2.5')
        persisted=json.loads(path.read_text())
        self.assertNotIn('api_key',persisted)
        self.assertNotIn('_api_key',persisted)
        self.assertFalse(assistant.exaone_api_key_path.exists())
        self.assertFalse(assistant.api_key_path.exists())


if __name__=='__main__':unittest.main()

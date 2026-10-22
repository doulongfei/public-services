"""运行器与控制 API 的边界检查，不调用云端模型。"""
import importlib.util
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import io
import threading
import time
import unittest
import urllib.error
import urllib.request


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent.parent / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / 'control').mkdir()
        (self.root / 'control/api-key').write_text('test-control-secret')
        (self.root / 'model-key').write_text('test-model-secret')
        (self.root / 'control/commands.json').write_text(json.dumps({'mode': 'running', 'feedback': ''}))
        self.cfg = {'model_key': str(self.root / 'model-key'), 'deadline': time.time()+1000, 'started_at': time.time(), 'round_timeout': 1800}
        (self.root / 'config.json').write_text(json.dumps(self.cfg))

    def tearDown(self):
        self.directory.cleanup()

    def test_deadline_is_not_reset_by_restart(self):
        runner = module('runner'); runner.BASE = self.root; runner.clean_container = lambda: None
        self.cfg['deadline'] = time.time()-1
        (self.root / 'config.json').write_text(json.dumps(self.cfg))
        runner.main(); runner.main()
        self.assertEqual(json.loads((self.root / 'public/state.json').read_text())['phase'], 'completed')
        self.assertEqual(json.loads((self.root / 'config.json').read_text())['deadline'], self.cfg['deadline'])

    def test_artifact_boundary(self):
        runner = module('runner'); candidate = self.root / 'candidate'; (candidate / 'game').mkdir(parents=True)
        for name in ['index.html', 'main.js', 'core.js', 'render.js', 'style.css']:
            (candidate / 'game' / name).write_text('safe')
        runner.validate_tree(candidate, ['private-secret'])
        target = candidate / 'game/main.js'
        for text in ['private-secret', 'fetch("https://example.com")', '<iframe>']:
            target.write_text(text)
            with self.assertRaises(ValueError): runner.validate_tree(candidate, ['private-secret'])
        target.unlink(); target.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): runner.validate_tree(candidate, [])

    def test_incremental_logs_do_not_accumulate(self):
        runner = module('runner')
        process = type('Process', (), {'stdout': io.StringIO('{"type":"message_update","partial":"large"}\n{"type":"message_end"}\nerror detail\n')})()
        output = io.StringIO(); runner.record_output(process, output)
        self.assertEqual(output.getvalue(), '{"type":"message_end"}\nerror detail\n')

    def test_control_auth_origin_pause_resume_and_feedback(self):
        control = module('control'); control.BASE = self.root
        server = ThreadingHTTPServer(('127.0.0.1', 0), control.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        url = f'http://127.0.0.1:{server.server_port}/api/control'
        def send(action, token='test-control-secret', origin='https://lab.doufei.eu.org', **extra):
            req = urllib.request.Request(url, data=json.dumps({'action': action, **extra}).encode(), headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json', 'Origin': origin})
            return json.load(urllib.request.urlopen(req, timeout=3))
        try:
            with self.assertRaises(urllib.error.HTTPError) as missing: send('pause', token='wrong')
            self.assertEqual(missing.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError) as foreign: send('pause', origin='https://example.com')
            self.assertEqual(foreign.exception.code, 403)
            self.assertEqual(send('pause')['mode'], 'paused')
            self.assertEqual(send('resume')['mode'], 'running')
            self.assertTrue(send('feedback', text='降低初始难度')['feedback_saved'])
            self.assertEqual(json.loads((self.root / 'control/commands.json').read_text())['feedback'], '降低初始难度')
            with self.assertRaises(urllib.error.HTTPError) as invalid: send('feedback', text='x'*2001)
            self.assertEqual(invalid.exception.code, 400)
            self.assertEqual(send('stop')['mode'], 'stopped')
            with self.assertRaises(urllib.error.HTTPError) as stopped: send('resume')
            self.assertEqual(stopped.exception.code, 409)
        finally:
            server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__':
    unittest.main()

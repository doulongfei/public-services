"""真实临时 Git 仓库与故障注入，不调用云模型，不修改部署数据。"""
import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from controller_test import module


class Workflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.runner = module('runner'); self.runner.BASE = self.base
        import release
        self.release = release
        (self.base / 'control').mkdir()
        (self.base / 'control/commands.json').write_text(json.dumps({'mode': 'running', 'feedback': ''}))
        (self.base / 'control/api-key').write_text('test-control')
        (self.base / 'model-key').write_text('test-model')
        (self.base / 'rounds').mkdir()
        seed = Path(__file__).parent.parent / 'seed'
        shutil.copytree(seed, self.base / 'history')
        for args in [('init', '-q'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.invalid'), ('add', '.'), ('commit', '-qm', 'baseline')]:
            release.git(self.base, *args)
        self.first = release.git(self.base, 'rev-parse', 'HEAD').strip()
        self.state = {'round': 0, 'accepted': 0, 'rejected': 0, 'tokens': 0, 'history': [], 'topic_index': 0, 'last_release': self.first[:12], 'release_id': '00000-'+self.first[:12]}
        release.stage(self.base, self.base / 'history', self.state['release_id'], self.first, self.state, {'checks': [], 'features': []}, self.base / 'absent')
        release.activate(self.base, self.state['release_id'], lambda: True)
        self.cfg = {'model_key': str(self.base / 'model-key'), 'started_at': time.time(), 'deadline': time.time()+10000, 'round_timeout': 1800, 'cooldown': 120}
        release.atomic(self.base / 'config.json', self.cfg)

    def tearDown(self):
        self.temp.cleanup()

    def prepared(self):
        target = self.base / 'rounds/00001/candidate'
        self.release.export_source(self.base, self.first, target)
        f = target / 'game/main.js'; f.write_text(f.read_text()+'\n// candidate\n')
        version = self.release.prepare_commit(self.base, target, self.first, 1, 'test')
        state = {**self.state, 'round': 1, 'accepted': 1, 'last_release': version[:12], 'release_id': '00001-'+version[:12]}
        self.release.stage(self.base, target, state['release_id'], version, state, {'checks': [], 'features': []}, self.base / 'absent')
        return target, version, state

    def test_prepare_does_not_advance_accepted_head_or_worktree(self):
        target, version, state = self.prepared()
        self.assertNotEqual(version, self.first)
        self.assertEqual(self.release.git(self.base, 'rev-parse', 'HEAD').strip(), self.first)
        self.assertNotEqual((target/'game/main.js').read_bytes(), (self.base/'history/game/main.js').read_bytes())
        self.assertEqual(self.release.current(self.base)['version'], self.first)

    def test_cancel_before_switch_keeps_old_baseline_and_checkpoint(self):
        _, version, state = self.prepared()
        cp = {'candidate': 'rounds/00001/candidate', 'code_failures': 2}
        self.release.atomic(self.base/'checkpoint.json', cp, 0o600)
        with self.assertRaises(InterruptedError):
            self.release.activate(self.base, state['release_id'], lambda: False)
        recovered = self.release.recover(self.base)
        self.assertEqual(recovered['accepted'], 0)
        self.assertEqual(self.release.current(self.base)['version'], self.first)
        self.assertEqual(json.loads((self.base/'checkpoint.json').read_text()), cp)
        self.assertEqual(self.release.git(self.base, 'rev-parse', 'HEAD').strip(), self.first)

    def test_crash_after_switch_recovers_exactly_once(self):
        _, version, state = self.prepared()
        with patch.object(self.release, 'finish', side_effect=OSError('simulated disk interruption')):
            with self.assertRaises(OSError): self.release.activate(self.base, state['release_id'], lambda: True)
        self.assertEqual(self.release.current(self.base)['version'], version)
        self.assertEqual(json.loads((self.base/'public/state.json').read_text())['accepted'], 0)
        for _ in range(2):
            self.assertEqual(self.release.recover(self.base)['accepted'], 1)
        self.assertEqual(self.release.git(self.base, 'rev-parse', 'HEAD').strip(), version)
        self.assertFalse((self.base/'pending.json').exists())

    def test_versioned_entry_and_legacy_assets_remain_immutable(self):
        old = (self.base/'public/current/game/index.html').read_text()
        _, version, state = self.prepared()
        self.release.activate(self.base, state['release_id'], lambda: True)
        self.release.prune(self.base)
        self.assertEqual((self.base/'public/releases'/self.state['release_id']/'game/index.html').read_text(), old)
        self.assertIn('/releases/'+state['release_id']+'/game/', (self.base/'public/current/game/index.html').read_text())
        with self.assertRaises(ValueError): self.release.versioned_html('<head><base href="/">', 'x')

    def write_log(self, reason='stop', ended=True, successful=True):
        events = [{'type': 'tool_execution_end', 'toolName': 'edit', 'isError': not successful},
                  {'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': reason, 'usage': {'totalTokens': 12}, 'content': [{'type': 'text', 'text': 'done'}]}}]
        if ended: events.append({'type': 'agent_end'})
        p = self.base/'pi.jsonl'; p.write_text('\n'.join(json.dumps(e) for e in events))
        return p

    def test_model_failures_and_truncation_are_rejected(self):
        for reason in ('error', 'aborted', 'length', 'toolUse', None):
            with self.subTest(reason=reason), self.assertRaises((self.runner.ModelIncomplete, self.runner.InfrastructureFailure)):
                self.runner.parse_agent_log(self.write_log(reason))
        with self.assertRaises(self.runner.ModelIncomplete): self.runner.parse_agent_log(self.write_log(ended=False))
        with self.assertRaises(self.runner.ModelIncomplete): self.runner.parse_agent_log(self.write_log(successful=False))
        self.assertEqual(self.runner.parse_agent_log(self.write_log()), ('done', 12, 1))

    def test_recovered_tool_error_is_allowed_only_with_final_success(self):
        p = self.write_log()
        p.write_text(json.dumps({'type': 'tool_execution_end', 'toolName': 'edit', 'isError': True})+'\n'+p.read_text())
        self.assertEqual(self.runner.parse_agent_log(p)[2], 2)

    def run_failed_attempt(self, error):
        def halt_wait(*_): self.runner.HALT = True
        self.runner.HALT = False
        # 模拟退避时间已到；实际候选与失败计数由运行器保存和恢复。
        for name in ('checkpoint.json', 'public/state.json'):
            p = self.base/name; d = json.loads(p.read_text());d['retry_at'] = 0;self.release.atomic(p,d)
        def fail_once(*_):
            self.runner.HALT = True
            if isinstance(error, self.runner.ModelIncomplete):
                (_[3]).write_text(json.dumps({'type': 'message_end', 'message': {'role': 'assistant', 'usage': {'totalTokens': 12, 'output': 3}}})+'\n')
            raise error
        with patch.object(self.runner, 'clean_container'), patch.object(self.runner, 'owner_tree'), patch.object(self.runner.shutil, 'disk_usage', return_value=SimpleNamespace(free=64*1024**3)), patch.object(self.runner, 'run_agent', side_effect=fail_once), patch.object(self.runner, 'wait', side_effect=halt_wait):
            self.runner.main()
        return json.loads((self.base/'checkpoint.json').read_text()), json.loads((self.base/'public/state.json').read_text())

    def test_restart_preserves_failed_candidate_counter_and_error(self):
        first, _ = self.run_failed_attempt(ValueError('code failed'))
        second, state = self.run_failed_attempt(ValueError('code failed again'))
        self.assertEqual(first['candidate'], second['candidate'])
        self.assertEqual(second['code_failures'], 2)
        self.assertIn('again', second['error'])
        self.assertEqual(state['topic_index'], 0)
        self.assertGreater(second['retry_at'], time.time())

    def test_three_code_failures_reset_candidate_without_skipping_task(self):
        first, _ = self.run_failed_attempt(ValueError('code failed'))
        self.run_failed_attempt(ValueError('code failed'))
        third, state = self.run_failed_attempt(ValueError('code failed'))
        self.assertNotIn('candidate', third)
        self.assertEqual(third['code_failures'], 3)
        self.assertEqual(state['topic_index'], 0)
        fourth, _ = self.run_failed_attempt(ValueError('code failed'))
        self.assertNotEqual(first['candidate'], fourth['candidate'])
        self.assertEqual(fourth['code_failures'], 4)

    def test_mixed_model_infra_and_code_failures_restore_baseline(self):
        first, _ = self.run_failed_attempt(self.runner.ModelIncomplete('budget exhausted'))
        self.run_failed_attempt(self.runner.InfrastructureFailure('connection refused'))
        third, _ = self.run_failed_attempt(ValueError('invalid code'))
        self.assertNotIn('candidate', third)
        self.assertEqual(third['attempt_failures'], 3)
        self.assertEqual(third['model_failures'], 1)
        fourth, _ = self.run_failed_attempt(self.runner.ModelIncomplete('budget exhausted'))
        self.assertNotEqual(first['candidate'], fourth['candidate'])
        self.assertEqual((self.base/fourth['candidate']/'game/index.html').read_bytes(), (self.base/'history/game/index.html').read_bytes())

    def test_damaged_candidate_is_discarded_before_another_model_attempt(self):
        first, _ = self.run_failed_attempt(self.runner.ModelIncomplete('aborted'))
        (self.base/first['candidate']/'game/index.html').write_text('placeholder')
        second, _ = self.run_failed_attempt(self.runner.ModelIncomplete('aborted'))
        self.assertNotEqual(first['candidate'], second['candidate'])
        self.assertEqual(second['resets'], 1)
        self.assertIn('<canvas', (self.base/second['candidate']/'game/index.html').read_text())

    def test_six_failures_defer_without_claiming_success_and_retry_after_queue(self):
        for _ in range(6): cp, state = self.run_failed_attempt(self.runner.ModelIncomplete('budget exhausted'))
        self.assertEqual(cp, {})
        self.assertEqual(state['accepted'], 0)
        self.assertEqual(state['topic_index'], 1)
        self.assertEqual(state['history'][-1]['result'], 'deferred')
        state['topic_index'] = len(self.runner.TOPICS)
        state['deferred_tasks'][0]['after'] = 0
        queued = self.runner.new_checkpoint(state, 7)
        self.assertEqual(queued['task'], 'pause-overlay')
        self.assertEqual(queued['next_cursor'], len(self.runner.TOPICS))

    def test_failed_attempt_usage_is_counted_and_budget_abort_is_not_infra(self):
        p = self.write_log(reason='error')
        data = p.read_text().replace('"totalTokens": 12', '"totalTokens": 12, "output": 3').replace('"stopReason": "error"', '"stopReason": "error", "errorMessage": "This operation was aborted"')
        p.write_text(data)
        self.assertEqual(self.runner.agent_usage(p), (12, 3))
        with self.assertRaises(self.runner.ModelIncomplete): self.runner.parse_agent_log(p)
        _, state = self.run_failed_attempt(self.runner.ModelIncomplete('budget exhausted'))
        self.assertEqual(state['tokens'], 12)
        self.assertEqual(state['output_tokens'], 3)

    def test_metadata_only_changes_are_not_game_source_changes(self):
        target = self.base/'rounds/metadata'
        shutil.copytree(self.base/'history', target, ignore=shutil.ignore_patterns('.git'))
        (target/'game/acceptance.json').write_text('{"task":"enemy-types"}')
        (target/'NOTES.md').write_text('claimed improvement')
        self.assertEqual(self.runner.fingerprint(target), self.runner.fingerprint(self.base/'history'))

    def test_task_prompt_includes_current_source_and_only_matching_contract(self):
        cp = {'cursor': 2, 'task': 'enemy-types'}
        prompt = self.runner.make_prompt(cp, self.base/'history')
        self.assertIn('export function createState()', prompt)
        self.assertIn('CURRENT /workspace/game/render.js', prompt)
        self.assertNotIn('score":100', prompt)
        self.assertNotIn('CURRENT /workspace/game/main.js', prompt)
        from acceptance import validate_check
        for _, _, check in self.runner.PLANS.values(): validate_check(check)

    def test_control_interruption_does_not_consume_code_retries(self):
        cp, state = self.run_failed_attempt(self.runner.Cancelled('paused'))
        self.assertEqual(cp['code_failures'], 0)
        self.assertEqual(cp['infra_failures'], 0)
        self.assertEqual(state['rejected'], 0)
        self.assertEqual(state['history'][-1]['result'], 'interrupted')
        self.assertEqual(state['topic_index'], 0)

    def test_infrastructure_failure_has_separate_persistent_backoff(self):
        cp, state = self.run_failed_attempt(self.runner.InfrastructureFailure('model unavailable'))
        self.assertEqual(cp['code_failures'], 0)
        self.assertEqual(cp['infra_failures'], 1)
        self.assertEqual(state['topic_index'], 0)
        self.assertEqual(state['history'][-1]['result'], 'retrying')
        self.assertEqual([self.runner.delay(i) for i in range(1,7)], [240,480,960,1920,3600,3600])

    def test_low_disk_wait_does_not_start_or_reject_a_round(self):
        def halt_wait(*_): self.runner.HALT = True
        with patch.object(self.runner, 'clean_container'), patch.object(self.runner.shutil, 'disk_usage', return_value=SimpleNamespace(free=1024**3)), patch.object(self.runner, 'run_agent') as worker, patch.object(self.runner, 'wait', side_effect=halt_wait):
            self.runner.main()
        state = json.loads((self.base/'public/state.json').read_text())
        worker.assert_not_called()
        self.assertEqual(state['round'], 0)
        self.assertEqual(state['rejected'], 0)

    def test_invalid_declarative_scenarios_are_rejected(self):
        from acceptance import validate_check
        for check in [{'kind': 'core', 'path': 'constructor.x', 'op': 'exists'},
                      {'kind': 'core', 'path': 'hp', 'op': 'gt', 'value': 0, 'steps': 1000000},
                      {'kind': 'ui', 'selector': 'script', 'property': 'text', 'op': 'exists'}]:
            with self.assertRaises(ValueError): validate_check(check)
        validate_check({'kind': 'core', 'path': 'level', 'op': 'gt', 'value': 1, 'initial': {'score': 100}})


if __name__ == '__main__': unittest.main()

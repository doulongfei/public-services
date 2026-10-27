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

    def test_rejected_tool_reason_reaches_next_attempt_instead_of_being_lost(self):
        p=self.write_log(successful=False)
        error={'type':'tool_execution_end','toolName':'write','isError':True,
               'result':{'content':[{'type':'text','text':'Acceptance JSON rejected before writing: checks must contain 1-4 checks.'}]}}
        p.write_text(json.dumps(error)+'\n'+p.read_text())
        with self.assertRaisesRegex(self.runner.ModelIncomplete,'checks must contain 1-4'):
            self.runner.parse_agent_log(p)
        p.write_text(json.dumps({'type':'guard_limit','reason':'repeated_tool_error'})+'\n'+p.read_text())
        with self.assertRaisesRegex(self.runner.ModelIncomplete,'last tool error: Acceptance JSON rejected'):
            self.runner.parse_agent_log(p)

    def test_guard_limit_surfaces_the_actual_retry_reason(self):
        p=self.write_log(reason='error')
        p.write_text(json.dumps({'type':'guard_limit','reason':'model_turns','limit':8})+'\n'+p.read_text())
        with self.assertRaisesRegex(self.runner.ModelIncomplete,'model_turns') as error:
            self.runner.parse_agent_log(p)
        self.assertIn('8 次回合',self.runner.public_failure(str(error.exception)))
        self.assertIn('固定验证',self.runner.public_failure('Assertion failed\nℹ cancelled 0\nNode.js v24'))
        self.assertIn('Elite telegraph',self.runner.public_failure('AssertionError [ERR_ASSERTION]: Elite telegraph must count down in core.step and expire\nstack'))
        self.assertIn('绘制时修改',self.runner.public_failure('page.evaluate: Error: Renderer must not mutate core state: playing'))

    def run_failed_attempt(self, error):
        def halt_wait(*_): self.runner.HALT = True
        self.runner.HALT = False
        # 模拟退避时间已到；实际候选与失败计数由运行器保存和恢复。
        for name in ('checkpoint.json', 'public/state.json'):
            p = self.base/name; d = json.loads(p.read_text());d['retry_at'] = 0;self.release.atomic(p,d)
        def fail_once(*_, **_kwargs):
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

    def test_third_ui_failure_keeps_validated_core_checkpoint(self):
        candidate=self.base/'rounds/00000/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        cp={'base_version':self.first,'cursor':3,'next_cursor':4,'task':'wave-feedback','stage':'generating',
            'candidate':'rounds/00000/candidate','generation_step':1,'stage_results':{'0':{'summary':'verified core','tools':1}},
            'code_failures':2,'infra_failures':0,'model_failures':0,'attempt_failures':2}
        self.release.atomic(self.base/'checkpoint.json',cp)
        state={**self.state,'topic_index':3};self.release.atomic(self.base/'public/state.json',state)
        result,state=self.run_failed_attempt(ValueError('UI assertion failed'))
        self.assertEqual(result['generation_step'],1)
        self.assertEqual(result['candidate'],cp['candidate'])
        self.assertEqual(result['stage_results']['0']['summary'],'verified core')
        self.assertEqual(state['topic_index'],3)

    def test_six_failures_wait_for_changed_baseline_instead_of_repeating_forever(self):
        for _ in range(6): cp, state = self.run_failed_attempt(self.runner.ModelIncomplete('budget exhausted'))
        self.assertEqual(cp, {})
        self.assertEqual(state['accepted'], 0)
        self.assertEqual(state['topic_index'], 1)
        self.assertEqual(state['history'][-1]['result'], 'deferred')
        state['topic_index'] = len(self.runner.TOPICS)
        state['deferred_tasks'][0]['after'] = 0
        queued = self.runner.new_checkpoint(state, 7)
        self.assertEqual(queued['task'], 'self-review')
        self.assertEqual(state['deferred_tasks'][0]['base_version'], self.first)
        # 真正的新发布后，固定任务才获得一次新的有界尝试机会。
        shutil.rmtree(self.base/'rounds/00001/candidate')
        _, version, next_state = self.prepared()
        self.release.activate(self.base, next_state['release_id'], lambda: True)
        state['last_release'] = version[:12]
        queued = self.runner.new_checkpoint(state, 8)
        self.assertEqual(queued['task'], 'pause-overlay')
        self.assertEqual(queued['next_cursor'], len(self.runner.TOPICS))

    def test_legacy_and_generic_deferred_tasks_cannot_starve_new_review(self):
        state={**self.state,'topic_index':17,'deferred_tasks':[
            {'cursor':8,'task':'difficulty','after':0},
            {'cursor':16,'task':'self-review','after':0,'base_version':'old'}]}
        cp=self.runner.new_checkpoint(state,1)
        self.assertEqual(cp['cursor'],17)
        self.assertEqual(cp['task'],'self-review')

    def test_review_changes_focus_after_three_failures_without_claiming_a_release(self):
        self.release.atomic(self.base/'public/state.json',{**self.state,'topic_index':17})
        for _ in range(3):cp,state=self.run_failed_attempt(self.runner.ModelIncomplete('blocked tool'))
        self.assertEqual(cp,{})
        self.assertEqual(state['accepted'],0)
        self.assertEqual(state['topic_index'],18)
        self.assertEqual(state.get('deferred_tasks'),[])
        self.assertEqual(self.runner.checkpoint_files(self.runner.new_checkpoint(state,4)),['index.html','style.css'])

    def test_review_prompt_scope_matches_generation_and_metadata_for_multiple_cycles(self):
        for cursor in range(15,35):
            cp={'task':'self-review','cursor':cursor,'base_version':self.first}
            files=self.runner.checkpoint_files(cp)
            prompt=self.runner.make_prompt(cp,self.base/'history')
            self.assertIn('Edit only '+', '.join('game/'+name for name in files),prompt)
            self.assertEqual(self.runner.generation_stages('self-review',cp),[files,['acceptance.json']])
            self.assertNotIn('Optional review lens:',prompt)
            self.assertNotIn('Modify ONLY game/main.js to add a pause overlay',prompt)
            metadata=self.runner.acceptance_prompt(cp,self.base/'history')
            self.assertIn('aria-pressed',prompt)
            self.assertIn('aria-pressed',metadata)
            if 'main.js' in files:self.assertIn('#shield-btn aria-label MUST remain 启动护盾',prompt)
            for name in files:
                self.assertIn('CURRENT /workspace/game/'+name,prompt)
                self.assertIn('ACTUAL DIFF '+name,metadata)

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

    def test_staged_wave_workflow_validates_core_before_ui_and_resumes_failed_stage(self):
        candidate = self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history', candidate, ignore=shutil.ignore_patterns('.git'))
        cp = {'task': 'wave-feedback', 'cursor': 3, 'stage': 'generating'}
        state = copy.deepcopy(self.state); state['current_task'] = cp['task']
        calls=[]
        def agent(_cfg, _candidate, prompt, _log, _state, files):
            calls.append(tuple(files))
            if files == ['main.js']: raise self.runner.ModelIncomplete('stage budget exhausted')
            self.assertNotIn('CURRENT /workspace/game/main.js', prompt)
            return 'core done', 0, 1
        def verify(*args, **kwargs):
            calls.append('verified')
            self.assertTrue(kwargs['core_only'])
        with patch.object(self.runner,'run_agent',side_effect=agent), patch.object(self.runner,'validate_runtime',side_effect=verify):
            with self.assertRaises(self.runner.ModelIncomplete):
                self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,state)
        self.assertEqual(calls,[('core.js',),'verified',('main.js',)])
        self.assertEqual(cp['generation_step'],1)
        self.assertEqual(json.loads((self.base/'checkpoint.json').read_text())['generation_step'],1)
        self.assertEqual(self.release.current(self.base)['version'],self.first,'A core stage must never publish on its own')
        (candidate/'game/main.js').write_text('unverified partial UI')
        (candidate/'game/core.js').write_text((candidate/'game/core.js').read_text()+'\n// verified core marker\n')
        cp['error']='UI stage interrupted'
        def resume(_cfg,_candidate,prompt,_log,_state,files):
            self.assertEqual(files,['main.js'])
            self.assertIn('ALREADY passed wave tests',prompt)
            self.assertIn('exactly TWO disjoint edits',prompt)
            self.assertNotIn('On a wave change set waveNotice',prompt)
            self.assertIn('Do not decrement life',prompt)
            self.assertEqual((candidate/'game/main.js').read_bytes(),(self.base/'history/game/main.js').read_bytes())
            self.assertIn('verified core marker',(candidate/'game/core.js').read_text())
            return 'UI done',0,2
        with patch.object(self.runner,'run_agent',side_effect=resume), patch.object(self.runner,'validate_runtime') as validator:
            summary,tools=self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,state)
        validator.assert_not_called()
        self.assertEqual(summary,'core done\nUI done')
        self.assertEqual(tools,3)

    def test_failed_core_contract_does_not_advance_to_ui(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        cp={'task':'wave-feedback','cursor':3,'stage':'generating'}
        state={**self.state,'current_task':'wave-feedback'}
        with patch.object(self.runner,'run_agent',return_value=('incomplete core',0,1)) as agent, patch.object(self.runner,'validate_runtime',side_effect=ValueError('waveNotice is missing')):
            with self.assertRaisesRegex(ValueError,'waveNotice'):
                self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,state)
        self.assertEqual(agent.call_count,1)
        self.assertEqual(cp.get('generation_step',0),0)

    def budget_log(self, logfile, writes=True, reason='model_turns'):
        logfile.write_text('\n'.join(json.dumps(e) for e in [
            {'type':'guard_limit','reason':reason},
            {'type':'tool_execution_end','toolName':'edit','isError':not writes}]))

    def test_budget_recovery_requires_authenticated_limit_successful_write_and_changed_file(self):
        candidate=self.base/'history';name='core.js';p=candidate/'game'/name;original=p.read_bytes()
        before={name:original};log=self.base/'budget.jsonl'
        for changed,writes,reason in [(False,True,'model_turns'),(True,False,'model_turns'),(True,True,'provider_error')]:
            p.write_bytes(original+(b'\n// edit\n' if changed else b''));self.budget_log(log,writes,reason)
            self.assertIsNone(self.runner.budget_candidate(log,candidate,before))
        self.budget_log(log);self.assertIsNotNone(self.runner.budget_candidate(log,candidate,before))

    def test_budget_candidate_must_pass_core_contract_before_advancing(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        cp={'task':'wave-feedback','cursor':3};state={**self.state,'current_task':'wave-feedback'};calls=[]
        def agent(_cfg,_candidate,_prompt,log,_state,files):
            calls.append(tuple(files))
            if files==['core.js']:
                p=candidate/'game/core.js';p.write_text(p.read_text()+'\n// generated edit\n')
                self.budget_log(log);raise self.runner.ModelIncomplete('Stage budget exhausted: model_turns')
            return 'UI finished',0,1
        with patch.object(self.runner,'run_agent',side_effect=agent),patch.object(self.runner,'validate_runtime',side_effect=ValueError('core contract failed')):
            with self.assertRaisesRegex(ValueError,'core contract failed'):
                self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,state)
        self.assertEqual(calls,[('core.js',)]);self.assertNotIn('0',cp['stage_results'])
        with patch.object(self.runner,'run_agent',side_effect=agent),patch.object(self.runner,'validate_runtime'):
            self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,state)
        self.assertEqual(cp['stage_results']['0']['completion'],'budget-recovered')
        self.assertEqual(cp['stage_results']['1']['completion'],'normal')
        self.assertEqual(self.release.current(self.base)['version'],self.first)

    def test_owner_cancel_is_never_recovered_even_if_log_contains_budget_marker(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        def cancel(_cfg,_candidate,_prompt,log,_state,files):
            p=candidate/'game/core.js';p.write_text(p.read_text()+'\n// edit\n');self.budget_log(log)
            raise self.runner.Cancelled('Round interrupted by owner control')
        with patch.object(self.runner,'run_agent',side_effect=cancel),patch.object(self.runner,'validate_runtime') as validator:
            with self.assertRaises(self.runner.Cancelled):
                self.runner.generate(self.cfg,{'task':'wave-feedback','cursor':3},candidate,self.base/'history',candidate.parent,{**self.state,'current_task':'wave-feedback'})
        validator.assert_not_called()

    def test_budget_recovery_cannot_publish_when_final_browser_validation_fails(self):
        self.release.atomic(self.base/'public/state.json',{**self.state,'topic_index':3})
        def agent(_cfg,candidate,_prompt,log,_state,files):
            p=candidate/'game'/files[0];p.write_text(p.read_text()+'\n// generated edit\n');self.budget_log(log)
            raise self.runner.ModelIncomplete('Stage budget exhausted: model_turns')
        def validate(*args,**kwargs):
            if not kwargs.get('core_only'):
                self.runner.HALT=True;raise ValueError('Wave UI not visible')
        with patch.object(self.runner,'run_agent',side_effect=agent),patch.object(self.runner,'validate_runtime',side_effect=validate),patch.object(self.runner,'clean_container'),patch.object(self.runner,'owner_tree'),patch.object(self.runner.shutil,'disk_usage',return_value=SimpleNamespace(free=64*1024**3)):
            self.runner.main()
        state=self.runner.load(self.base/'public/state.json');cp=self.runner.load(self.base/'checkpoint.json')
        self.assertEqual(state['accepted'],0);self.assertEqual(state['history'][-1]['result'],'rejected')
        self.assertEqual(cp['generation_step'],1);self.assertEqual(cp['stage_results']['0']['completion'],'budget-recovered')
        self.assertEqual(self.release.current(self.base)['version'],self.first)

    def test_broken_middle_controls_do_not_get_checkpointed_as_verified(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        cp={'task':'shield','cursor':11,'stage':'generating'}
        state={**self.state,'current_task':'shield'};calls=[]
        def agent(_cfg,_candidate,_prompt,_log,_state,files):
            calls.append(tuple(files));return 'done',0,1
        def validate(*args,**kwargs):
            calls.append('core-check' if kwargs.get('core_only') else 'controls-check')
            if not kwargs.get('core_only'):
                self.assertIn('shield',args[4]['features'])
                raise ValueError('Shield controls broke retained UI')
        with patch.object(self.runner,'run_agent',side_effect=agent),patch.object(self.runner,'validate_runtime',side_effect=validate):
            with self.assertRaisesRegex(ValueError,'controls broke'):
                self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,state)
        self.assertEqual(calls,[('core.js',),'core-check',('main.js',),'controls-check'])
        self.assertEqual(cp['generation_step'],1)
        self.assertEqual(cp['stage_results']['0']['summary'],'done')

    def test_final_core_regression_routes_back_to_core_and_keeps_completed_render(self):
        self.release.atomic(self.base/'public/state.json',{**self.state,'topic_index':10})
        def agent(_cfg,candidate,_prompt,_log,_state,files):
            p=candidate/'game'/files[0];p.write_text(p.read_text()+'\n// completed model change\n');return 'done',0,1
        def validate(*args,**kwargs):
            if not kwargs.get('core_only'):
                self.runner.HALT=True;raise ValueError('test at /harness/core.test.mjs:20: fatal collision regression')
        with patch.object(self.runner,'run_agent',side_effect=agent),patch.object(self.runner,'validate_runtime',side_effect=validate),patch.object(self.runner,'clean_container'),patch.object(self.runner,'owner_tree'),patch.object(self.runner.shutil,'disk_usage',return_value=SimpleNamespace(free=64*1024**3)):
            self.runner.main()
        cp=self.runner.load(self.base/'checkpoint.json')
        self.assertEqual(cp['generation_step'],0);self.assertNotIn('0',cp['stage_results'])
        self.assertEqual(cp['stage_results']['1']['completion'],'normal')
        self.assertEqual(self.release.current(self.base)['version'],self.first)

    def test_core_repair_reuses_unchanged_model_render_and_requires_final_publication_gate(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        render=candidate/'game/render.js';render.write_text(render.read_text()+'\n// generated render\n')
        cp={'task':'boss','cursor':10,'generation_step':0,'error':'/harness/core.test.mjs failed',
            'stage_results':{'1':{'summary':'render finished','tools':1,'completion':'normal','source_hashes':self.runner.stage_hashes(candidate,['render.js'])}}}
        def agent(_cfg,_candidate,_prompt,_log,_state,files):
            self.assertEqual(files,['core.js']);p=candidate/'game/core.js';p.write_text(p.read_text()+'\n// repaired core\n');return 'core repaired',0,1
        with patch.object(self.runner,'run_agent',side_effect=agent) as model,patch.object(self.runner,'validate_runtime'):
            self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,{**self.state,'current_task':'boss'})
        self.assertEqual(model.call_count,1);self.assertTrue(cp['stage_results']['1']['reused'])
        self.assertEqual(cp['generation_step'],2);self.assertIn('generated render',render.read_text())
        self.assertEqual(self.release.current(self.base)['version'],self.first)

    def test_changed_render_hash_is_not_reused(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        cp={'task':'boss','cursor':10,'generation_step':1,
            'stage_results':{'0':{'summary':'core finished','tools':1},'1':{'summary':'old render','tools':1,'completion':'normal','source_hashes':self.runner.stage_hashes(candidate,['render.js'])}}}
        render=candidate/'game/render.js';render.write_text(render.read_text()+'\n// unverified change\n')
        with patch.object(self.runner,'run_agent',return_value=('new render',0,1)) as model,patch.object(self.runner,'validate_runtime'):
            self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,{**self.state,'current_task':'boss'})
        model.assert_called_once();self.assertFalse(cp['stage_results']['1']['reused'])

    def test_task_prompt_includes_current_source_and_only_matching_contract(self):
        cp = {'cursor': 2, 'task': 'enemy-types'}
        prompt = self.runner.make_prompt(cp, self.base/'history')
        self.assertIn('export function createState()', prompt)
        self.assertIn('CURRENT /workspace/game/render.js', prompt)
        self.assertNotIn('score":100', prompt)
        self.assertNotIn('CURRENT /workspace/game/main.js', prompt)
        from acceptance import validate_check
        for _, _, check in self.runner.PLANS.values(): validate_check(check)

    def test_generic_source_is_validated_before_read_only_acceptance_stage(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        cp={'task':'self-review','cursor':15,'base_version':self.first,'stage':'generating'}
        state={**self.state,'current_task':'self-review'};calls=[]
        def agent(_cfg,_candidate,prompt,_log,_state,files):
            calls.append(tuple(files))
            if files==['acceptance.json']:
                self.assertIn('ACTUAL DIFF core.js',prompt)
                self.assertIn('steps integer 1-4000 (never 0)',prompt)
                self.assertIn('playing after starting for 64ms',prompt)
                self.assertIn('omit it for late-only render changes',prompt)
                self.assertNotIn('selector:"#id"',prompt)
                (candidate/'game/acceptance.json').write_text(json.dumps({'task':'self-review','checks':[{'kind':'core','path':'hp','op':'eq','value':90}]}))
            else:
                self.assertIn('Do not write acceptance.json',prompt)
                core=candidate/'game/core.js';core.write_text(core.read_text().replace('hp:100','hp:90'))
            return 'done',0,1
        def verify(_cfg,_candidate,_source,_output,suite):
            calls.append('regression');self.assertEqual(suite['new_checks'],[])
        with patch.object(self.runner,'run_agent',side_effect=agent),patch.object(self.runner,'validate_runtime',side_effect=verify):
            self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,state)
        self.assertEqual(calls,[('core.js',),'regression',('acceptance.json',)])
        self.assertEqual(cp['generation_step'],2)
        self.assertEqual(self.release.current(self.base)['version'],self.first)

    def test_invalid_acceptance_retries_without_regenerating_valid_source(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        core=candidate/'game/core.js';core.write_text(core.read_text()+'\n// already validated\n')
        cp={'task':'self-review','cursor':15,'base_version':self.first,'generation_step':1,
            'stage_results':{'0':{'summary':'source passed','tools':1}},'error':'Core steps must be 1-4000'}
        state={**self.state,'current_task':'self-review'}
        def metadata(_cfg,_candidate,_prompt,_log,_state,files):
            self.assertEqual(files,['acceptance.json'])
            self.assertIn('already validated',core.read_text())
            (candidate/'game/acceptance.json').write_text(json.dumps({'task':'self-review','checks':[{'kind':'core','path':'hp','steps':0,'op':'gt','value':0}]}))
            return 'invalid metadata',0,1
        with patch.object(self.runner,'run_agent',side_effect=metadata),patch.object(self.runner,'validate_runtime') as validator:
            with self.assertRaisesRegex(ValueError,'Core steps'):
                self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,state)
        validator.assert_not_called();self.assertEqual(cp['generation_step'],1)

    def test_measured_proof_skips_model_metadata_and_binds_all_source_hashes(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        cp={'task':'self-review','cursor':17,'base_version':self.first};state={**self.state,'current_task':'self-review'}
        check={'kind':'ui','selector':'#arena','property':'attribute','attribute':'title','op':'eq','value':'新帮助'}
        def agent(_cfg,_candidate,_prompt,_log,_state,files):
            self.assertEqual(files,['main.js']);p=candidate/'game/main.js';p.write_text(p.read_text()+"\ncanvas.title='新帮助';\n");return '实现帮助',0,1
        def validate(_cfg,_candidate,_source,output,suite):
            self.assertTrue(suite['discover_proofs']);output.mkdir(parents=True);self.runner.atomic(output/'proof-catalog.json',{'checks':[check]})
        with patch.object(self.runner,'run_agent',side_effect=agent) as model,patch.object(self.runner,'validate_runtime',side_effect=validate):
            self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,state)
        self.assertEqual(model.call_count,1);self.assertEqual(cp['stage_results']['1']['completion'],'measured')
        self.assertEqual(self.runner.proposal(candidate,'self-review'),[check])
        self.assertEqual(self.runner.measured_checks(cp,candidate),[check])
        p=candidate/'game/core.js';p.write_text(p.read_text()+'\n// unrelated change\n')
        with self.assertRaisesRegex(ValueError,'Measured proof source changed'):self.runner.measured_checks(cp,candidate)
        self.assertEqual(self.runner.failed_generation_step(cp,'Measured proof source changed'),0)

    def test_review_remembers_a_failed_approach_on_same_baseline_without_private_details(self):
        cp={'task':'self-review','cursor':18,'base_version':self.first}
        state=copy.deepcopy(self.state)
        self.runner.remember_failure(state,cp,'Control contract: private details test-control')
        self.assertNotIn('test-control',json.dumps(state))
        self.runner.atomic(self.base/'public/state.json',state)
        prompt=self.runner.make_prompt(cp,self.base/'history')
        self.assertIn('SAME published source',prompt);self.assertIn('Never add a static label',prompt)
        state['review_feedback']['index.html,style.css']['baseline']='different'
        self.runner.atomic(self.base/'public/state.json',state)
        self.assertNotIn('SAME published source',self.runner.make_prompt(cp,self.base/'history'))

    def test_unchanged_ui_output_does_not_ask_model_to_invent_proof(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        def agent(_cfg,_candidate,_prompt,_log,_state,files):
            p=candidate/'game/main.js';p.write_text(p.read_text()+'\n// no observable improvement\n');return 'done',0,1
        def validate(_cfg,_candidate,_source,output,_suite):
            output.mkdir(parents=True);self.runner.atomic(output/'proof-catalog.json',{'checks':[]})
        with patch.object(self.runner,'run_agent',side_effect=agent) as model,patch.object(self.runner,'validate_runtime',side_effect=validate):
            with self.assertRaisesRegex(ValueError,'No measured UI/Canvas improvement'):
                self.runner.generate(self.cfg,{'task':'self-review','cursor':17},candidate,self.base/'history',candidate.parent,{**self.state,'current_task':'self-review'})
        self.assertEqual(model.call_count,1)

    def test_measured_proof_cannot_bypass_final_publication_regression(self):
        self.release.atomic(self.base/'public/state.json',{**self.state,'topic_index':17})
        check={'kind':'ui','selector':'#arena','property':'attribute','attribute':'title','op':'eq','value':'帮助'}
        def agent(_cfg,candidate,_prompt,_log,_state,files):
            self.assertEqual(files,['main.js']);p=candidate/'game/main.js';p.write_text(p.read_text()+"\ncanvas.title='帮助';\n");return 'done',0,1
        def validate(_cfg,_candidate,_source,output,suite,**_kwargs):
            if suite.get('discover_proofs'):
                output.mkdir(parents=True);self.runner.atomic(output/'proof-catalog.json',{'checks':[check]})
            else:
                self.runner.HALT=True;raise ValueError('final browser regression failed')
        with patch.object(self.runner,'run_agent',side_effect=agent) as model,patch.object(self.runner,'validate_runtime',side_effect=validate),patch.object(self.runner,'clean_container'),patch.object(self.runner,'owner_tree'),patch.object(self.runner.shutil,'disk_usage',return_value=SimpleNamespace(free=64*1024**3)):
            self.runner.main()
        state=self.runner.load(self.base/'public/state.json')
        self.assertEqual(model.call_count,1);self.assertEqual(state['accepted'],0)
        self.assertEqual(self.release.current(self.base)['version'],self.first)

    def test_unchanged_source_never_reaches_acceptance_stage(self):
        candidate=self.base/'rounds/00001/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        cp={'task':'self-review','cursor':15,'base_version':self.first}
        with patch.object(self.runner,'run_agent',return_value=('claimed change',0,1)) as agent,patch.object(self.runner,'validate_runtime') as validator:
            with self.assertRaisesRegex(ValueError,'No game source changes'):
                self.runner.generate(self.cfg,cp,candidate,self.base/'history',candidate.parent,{**self.state,'current_task':'self-review'})
        self.assertEqual(agent.call_count,1);validator.assert_not_called()

    def test_no_improvement_proof_returns_to_implementation_instead_of_inventing_checks(self):
        candidate=self.base/'rounds/00000/candidate'
        shutil.copytree(self.base/'history',candidate,ignore=shutil.ignore_patterns('.git'))
        cp={'base_version':self.first,'cursor':15,'next_cursor':16,'task':'self-review',
            'candidate':'rounds/00000/candidate','stage':'generating','generation_step':1,
            'stage_results':{'0':{'summary':'source passed regression','tools':1}}}
        self.release.atomic(self.base/'checkpoint.json',cp)
        result,state=self.run_failed_attempt(ValueError('No new acceptance check fails on the published baseline'))
        self.assertNotIn('candidate',result);self.assertEqual(result['generation_step'],0)
        self.assertEqual(state['accepted'],0)

    def test_accessibility_contract_can_assert_real_dom_attributes(self):
        from acceptance import validate_check
        validate_check(self.runner.PLANS['accessibility'][2])
        validate_check({'kind':'ui','selector':'#shield-btn','property':'attribute','attribute':'aria-pressed','op':'eq','value':'false'})
        with self.assertRaisesRegex(ValueError,'attribute'):
            validate_check({'kind':'ui','selector':'#arena','property':'attribute','attribute':'onclick','op':'exists'})
        self.assertEqual(self.runner.generation_stages('accessibility'),[['main.js']])
        self.assertEqual(self.runner.generation_stages('onboarding'),[['index.html']])
        self.assertIn('15 kills',self.runner.PLANS['onboarding'][1])

    def test_control_interruption_does_not_consume_code_retries(self):
        cp, state = self.run_failed_attempt(self.runner.Cancelled('paused'))
        self.assertEqual(cp['code_failures'], 0)
        self.assertEqual(cp['infra_failures'], 0)
        self.assertEqual(state['rejected'], 0)
        self.assertEqual(state['history'][-1]['result'], 'interrupted')
        self.assertEqual(state['topic_index'], 0)

    def test_container_exit_racing_with_pause_is_not_an_infrastructure_failure(self):
        self.release.atomic(self.base/'control/commands.json',{'mode':'paused','feedback':''})
        process=SimpleNamespace(stdout=io.StringIO(''),poll=lambda:143,returncode=143)
        cfg={**self.cfg,'runtime_image':'fixture'}
        with patch.object(self.runner.subprocess,'Popen',return_value=process):
            with self.assertRaises(self.runner.Cancelled):
                self.runner.run_agent(cfg,self.base/'history','prompt',self.base/'pause-race.jsonl',{'current_task':'wave-feedback'})

    def test_test_container_exit_racing_with_pause_preserves_the_same_boundary(self):
        self.release.atomic(self.base/'control/commands.json',{'mode':'paused','feedback':''})
        process=SimpleNamespace(poll=lambda:143,returncode=143)
        cfg={**self.cfg,'runtime_image':'fixture'}
        with patch.object(self.runner.subprocess,'Popen',return_value=process),patch.object(self.runner.os,'chown'):
            with self.assertRaises(self.runner.Cancelled):
                self.runner.validate_runtime(cfg,self.base/'history',self.base/'history',self.base/'test-race-output',{'checks':[],'features':[]})

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

    def test_ui_checks_cannot_silently_ignore_core_inputs_or_custom_actions(self):
        from acceptance import validate_check
        check={'kind':'ui','selector':'#shield-btn','phase':'playing','property':'attribute','attribute':'aria-pressed','op':'eq','value':'false'}
        validate_check(check)
        for extra in ({'input':{'shield':True}}, {'initial':{'shield':{'active':True}}}, {'steps':30}, {'actions':['click']}):
            with self.assertRaisesRegex(ValueError,'do not simulate custom inputs'):
                validate_check({**check,**extra})
        with self.assertRaisesRegex(ValueError,'Unsupported visual check fields'):
            validate_check({'kind':'visual','selector':'#arena','input':{'shield':True}})


if __name__ == '__main__': unittest.main()

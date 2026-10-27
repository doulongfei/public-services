"""复现给开始按钮添加静态名称、另造重开按钮和无效比较值。"""
from pathlib import Path
import shutil
import tempfile
import unittest
from controller_test import module


class Contracts(unittest.TestCase):
    def test_start_button_keeps_one_dynamic_accessible_name(self):
        contracts = module('contracts')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(Path(__file__).parent.parent/'seed', root/'candidate')
            target = root/'candidate/game/index.html'
            original = target.read_text()
            contracts.validate_controls(root/'candidate')
            for attribute in ['aria-label="开始游戏 →"', 'aria-labelledby="guide"', 'hidden', 'disabled']:
                target.write_text(original.replace('id="start"', 'id="start" '+attribute))
                with self.assertRaisesRegex(ValueError, 'SAME button'):
                    contracts.validate_controls(root/'candidate')
            for added in ['<button id="restart">再次出航 →</button>', '<p id="arena">重复</p>']:
                target.write_text(original.replace('</body>', added+'</body>'))
                with self.assertRaisesRegex(ValueError, 'Control contract'):
                    contracts.validate_controls(root/'candidate')

    def test_comparison_values_are_required_and_typed(self):
        acceptance = module('acceptance')
        for check in [
            {'kind':'core','path':'wave','op':'gte'},
            {'kind':'core','path':'wave','op':'gte','value':None},
            {'kind':'core','path':'wave','op':'gte','value':True},
            {'kind':'ui','selector':'#arena','property':'attribute','attribute':'aria-pressed','op':'eq','value':False},
        ]:
            with self.assertRaises(ValueError):acceptance.validate_check(check)
        acceptance.validate_check({'kind':'core','path':'wave','op':'gte','value':1})
        acceptance.validate_check({'kind':'core','path':'wave','op':'exists'})

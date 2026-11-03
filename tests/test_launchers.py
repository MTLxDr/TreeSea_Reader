import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_windows_batch_files_have_crlf_and_forward_arguments(self):
        for name in ['启动树海Reader.cmd','启动数值计算器.cmd','启动效果库.cmd','启动数值接口.cmd','start_gui.cmd','start_console.cmd']:
            raw=(ROOT/name).read_bytes()
            self.assertEqual(raw.count(b'\n'),raw.count(b'\r\n'),name)
            self.assertIn(b'%*',raw,name)
            raw.decode('ascii')

    def test_unified_calculation_cli_preserves_unicode_input(self):
        data={'attacker':{'atk':{'base':{'value':1000,'source':{'kind':'manual','name':'启动测试白值'}}}},'defender':{'def':{'base':300}}}
        run=subprocess.run([sys.executable,str(ROOT/'launch_reader.py'),'--tool','calculate'],input=json.dumps(data,ensure_ascii=False).encode('utf-8'),capture_output=True,timeout=30,cwd=ROOT/'tests')
        self.assertEqual(run.returncode,0,run.stderr.decode('utf-8','replace'))
        result=json.loads(run.stdout)
        self.assertEqual(result['hp_damage'],700)
        self.assertEqual(result['attributes']['attacker']['atk']['trace'][0]['contributors'][0]['source']['name'],'启动测试白值')

    @unittest.skipUnless(sys.platform=='win32' and (ROOT/'TreeSeaReaderCLI.exe').is_file(),'Native launcher is not built')
    def test_native_cli_resolves_relative_paths_in_callers_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'中文 输入.json').write_text(json.dumps({'attacker':{'atk':{'base':1000}},'defender':{'def':{'base':300}}}),encoding='utf-8')
            run=subprocess.run([str(ROOT/'TreeSeaReaderCLI.exe'),'--tool','calculate','--input','中文 输入.json','--output','中文 结果.json'],cwd=path,capture_output=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stderr.decode('utf-8','replace'))
            self.assertEqual(json.loads((path/'中文 结果.json').read_text(encoding='utf-8'))['hp_damage'],700)


if __name__=='__main__':unittest.main()

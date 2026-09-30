"""Public synthetic templates only; never reads the real Agent configuration."""
import importlib.util,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import dr
spec=importlib.util.spec_from_file_location('mapping_check',ROOT/'scripts/verify-agent-dr-mappings.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class MappingTests(unittest.TestCase):
 def test_service_folder(self):
  template='{{ range secret "synthetic-project" "prod" "/aiometadata" }}{{ .Key }}={{ printf "%q" .Value }}\n{{ end }}'
  self.assertEqual(m.extract(template),{'folders':['/aiometadata'],'overrides':{}})
 def test_shared_alias(self):
  template='{{ range secret "synthetic-project" "prod" "/aiostreams" }}{{ .Key }}={{ .Value }}\n{{ end }}\n{{ range secret "synthetic-project" "prod" "/shared" }}{{ if eq .Key "JACKETT_API_KEY" }}BUILTIN_JACKETT_API_KEY={{ printf "%q" .Value }}\n{{ end }}{{ end }}'
  self.assertEqual(m.extract(template),{'folders':['/aiostreams'],'overrides':{'BUILTIN_JACKETT_API_KEY':{'path':'/shared','key':'JACKETT_API_KEY'}}})
 def test_unknown_syntax_refused(self):
  with self.assertRaises(dr.Refused):m.extract('{{ unsupportedFunction .Value }}')
if __name__=='__main__':unittest.main()

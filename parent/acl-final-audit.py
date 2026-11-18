import ast
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git', 'ls-files', '-co', '--exclude-standard', '-z', '--', '*.py'], cwd=root).decode().split('\0')
errors = []
count = 0
for name in set(paths):
    if not name or name.startswith('parent/'):
        continue
    path = root / name
    if not path.is_file():
        continue
    try:
        tree = ast.parse(path.read_bytes(), filename=name)
    except SyntaxError:
        continue
    count += 1
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'hermes_dir_acl':
            errors.append((name, node.lineno))
        if isinstance(node, ast.Import) and any(n.name == 'hermes_dir_acl' for n in node.names):
            errors.append((name, node.lineno))
print('parsed Python files:', count, 'retired imports:', errors)
assert not errors

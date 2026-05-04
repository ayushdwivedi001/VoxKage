import sys

file_path = 'voxkage/mcp_servers/gui_server.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.read().split('\n')

start_idx = -1
for i, line in enumerate(lines):
    if line.startswith('def gui_step('):
        start_idx = i
        break

if start_idx == -1:
    print('def gui_step not found')
    sys.exit(1)

doc_end = -1
for i in range(start_idx, len(lines)):
    if '    \"\"\"' in lines[i] and i > start_idx + 15:
        doc_end = i
        break

if doc_end == -1:
    print('docstring end not found')
    sys.exit(1)

lines.insert(doc_end + 1, '    with _GUI_LOCK:')
for i in range(doc_end + 2, len(lines)):
    if lines[i].startswith('def ') or lines[i].startswith('@mcp.tool'):
        break
    if lines[i] != '':
        lines[i] = '    ' + lines[i]

with open(file_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print('Indented gui_step successfully.')

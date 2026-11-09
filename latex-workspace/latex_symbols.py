"""Read local bibliography keys and labels without compiling or calling an AI API."""
import os
from pathlib import Path
import re

MAX_FILE = 4 * 1024 * 1024


def group(text, start, opening='{', closing='}'):
    if start >= len(text) or text[start] != opening:
        return '', start
    depth, index = 1, start + 1
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if opening == '(' and char == '{':
            _, index = group(text, index)
            continue
        if opening == '(' and char == '"':
            _, index = bib_value(text, index, {})
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
        index += 1
    return text[start + 1:], len(text)


def plain(text):
    text = re.sub(r'\\[\'"`^~=.uvHckbdtr]\s*\{?([A-Za-z])\}?', r'\1', text)
    text = re.sub(r'\\(?:[A-Za-z]+)\*?\s*', '', text)
    return re.sub(r'\s+', ' ', text.replace('{', '').replace('}', '').replace('\\&', '&').replace('~', ' ')).strip()[:500]


def bib_value(text, start, macros):
    parts, index = [], start
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        if text[index] == '{':
            value, index = group(text, index)
        elif text[index] == '"':
            begin = index = index + 1
            depth = 0
            while index < len(text):
                char = text[index]
                if char == '\\':
                    index += 2
                    continue
                if char == '{': depth += 1
                elif char == '}': depth = max(0, depth - 1)
                elif char == '"' and depth == 0: break
                index += 1
            value = text[begin:index]
            index = min(len(text), index + 1)
        else:
            match = re.match(r'[^\s,#}]+', text[index:])
            if not match:
                break
            value = match[0]
            index += len(value)
            value = macros.get(value.lower(), value)
        parts.append(value)
        while index < len(text) and text[index].isspace(): index += 1
        if index >= len(text) or text[index] != '#': break
        index += 1
    return ''.join(parts), index


def bib_fields(body, macros):
    result, index = {}, 0
    while index < len(body):
        match = re.match(r'[\s,]*([\w-]+)\s*=\s*', body[index:])
        if not match:
            index += 1
            continue
        key = match[1].lower()
        index += len(match[0])
        value, next_index = bib_value(body, index, macros)
        result[key] = value
        index = max(index + 1, next_index)
    return result


def bibliography(content, path):
    content = re.sub(r'(?m)^\s*%[^\n]*', '', content)
    entries, macros, index = [], {}, 0
    while True:
        match = re.search(r'@\s*([\w-]+)\s*([({])', content[index:])
        if not match:
            break
        at = index + match.start()
        opening = index + match.end() - 1
        body, index = group(content, opening, match[2], '}' if match[2] == '{' else ')')
        kind = match[1].lower()
        if kind == 'string':
            macros.update(bib_fields(body, macros))
            continue
        if kind in ('comment', 'preamble'):
            continue
        key, sep, fields = body.partition(',')
        key = key.strip()
        if not sep or not key or re.search(r'[\s{}]', key):
            continue
        metadata = bib_fields(fields, macros)
        entries.append({'kind': 'citation', 'key': key, 'title': plain(metadata.get('title', '')),
                        'author': plain(metadata.get('author', metadata.get('editor', ''))),
                        'year': plain(metadata.get('year', metadata.get('date', ''))),
                        'path': path, 'line': content.count('\n', 0, at) + 1})
    return entries


def tex_content(content):
    # Preserve all offsets, including newlines, while ignoring comments and verbatim.
    def blank(match): return re.sub(r'[^\n]', ' ', match[0])
    content = re.sub(r'(?<!\\)\\begin\{(verbatim\*?|lstlisting|minted|comment)\}.*?\\end\{\1\}', blank, content, flags=re.S)
    content = re.sub(r'\\verb\*?([^\w\s]).*?\1', blank, content)
    rows = []
    for line in content.splitlines(keepends=True):
        for index, char in enumerate(line):
            if char != '%': continue
            before = index
            while before > 0 and line[before - 1] == '\\': before -= 1
            if (index - before) % 2 == 0:
                line = line[:index] + re.sub(r'[^\n]', ' ', line[index:])
                break
        rows.append(line)
    return ''.join(rows)


def tex_symbols(content, path):
    content = tex_content(content)
    entries, headings = [], []
    for match in re.finditer(r'(?<!\\)\\(?:part|chapter|(?:sub)*section|caption)\*?\s*(?:\[[^\]]*\]\s*)?\{', content):
        title, _ = group(content, match.end() - 1)
        headings.append((match.start(), plain(title)))
    for match in re.finditer(r'(?<!\\)\\(label|bibitem)\s*(?:\[[^\]]*\]\s*)?\{', content):
        key, end = group(content, match.end() - 1)
        key = key.strip()
        if not key or re.search(r'[\s{}]', key): continue
        kind = 'label' if match[1] == 'label' else 'citation'
        title = next((title for offset, title in reversed(headings) if offset < match.start()), '') if kind == 'label' else plain(content[end:content.find('\n', end) if '\n' in content[end:] else end + 300])
        entries.append({'kind': kind, 'key': key, 'title': title, 'author': '', 'year': '',
                        'path': path, 'line': content.count('\n', 0, match.start()) + 1})
    return entries


class CompletionIndex:
    def __init__(self, workspace, error):
        self.workspace, self.error, self.cache = workspace, error, {}

    def collect(self, project, buffers=None):
        root = self.workspace.project(project)
        if buffers is None: buffers = {}
        if not isinstance(buffers, dict) or len(buffers) > 50:
            raise self.error('자동완성 문맥 형식이 올바르지 않습니다.')
        overlays, total = {}, 0
        for path, content in buffers.items():
            file = self.workspace.safe_path(project, path)
            if file.suffix.lower() not in ('.tex', '.bib') or not file.is_file() or not isinstance(content, str):
                raise self.error('프로젝트 안의 .tex 또는 .bib 파일만 자동완성에 사용할 수 있습니다.')
            size = len(content.encode('utf-8'))
            total += size
            if size > MAX_FILE or total > 6 * 1024 * 1024:
                raise self.error('자동완성 문맥 크기를 줄여 주세요.', 413)
            overlays[path] = content
        entries, warnings, seen_files, total = [], [], set(), 0
        for directory, dirs, names in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirs[:] = sorted(name for name in dirs if not name.startswith('.') and name not in ('node_modules', 'latex-workspace', 'tests') and not (parent / name).is_symlink())
            for name in sorted(names):
                file = parent / name
                if name.startswith('.') or file.suffix.lower() not in ('.tex', '.bib') or file.is_symlink(): continue
                relative = file.relative_to(root).as_posix()
                if relative in seen_files: continue
                seen_files.add(relative)
                try:
                    file = self.workspace.safe_path(project, relative)
                    stat = file.stat()
                    if stat.st_size > MAX_FILE or total + stat.st_size > 24 * 1024 * 1024:
                        warnings.append(relative + ': 파일 크기 제한으로 제외'); continue
                    total += stat.st_size
                    signature = (project, relative, stat.st_mtime_ns, stat.st_size)
                    if relative in overlays:
                        content = overlays[relative]
                        parsed = bibliography(content, relative) if file.suffix.lower() == '.bib' else tex_symbols(content, relative)
                    elif signature in self.cache:
                        parsed = self.cache[signature]
                    else:
                        content = file.read_text(encoding='utf-8', errors='replace')
                        parsed = bibliography(content, relative) if file.suffix.lower() == '.bib' else tex_symbols(content, relative)
                        self.cache = {key: value for key, value in self.cache.items() if key[:2] != signature[:2]}
                        self.cache[signature] = parsed
                    entries.extend(parsed)
                except OSError:
                    warnings.append(relative + ': 읽을 수 없음')
        # Multiple definitions remain one completion; expose all their source locations.
        unique = {}
        for item in entries:
            key = (item['kind'], item['key'])
            if key in unique:
                unique[key].setdefault('also_defined', []).append({'path': item['path'], 'line': item['line']})
            else:
                unique[key] = dict(item)
        return {'items': sorted(unique.values(), key=lambda item: (item['kind'], item['key'].casefold())), 'warnings': warnings}

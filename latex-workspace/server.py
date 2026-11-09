#!/usr/bin/env python3
"""A dependency-free local LaTeX workspace. Run: python server.py"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

APP = Path(__file__).resolve().parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from ai_assistant import AIAssistant, AssistantError
from sentence_comments import SentenceComments
from latex_symbols import CompletionIndex
TEXT_EXTENSIONS = {'.tex', '.bib', '.sty', '.cls', '.bst', '.txt', '.md', '.csv', '.json', '.yaml', '.yml', '.cfg', '.def', '.clo'}
IGNORED = {'.git', '__pycache__', 'node_modules', 'latex-workspace', 'tests'}
ENGINES = {'pdflatex': '-pdf', 'lualatex': '-lualatex', 'xelatex': '-xelatex'}


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def digest(content):
    return hashlib.sha256(content).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class Workspace:
    def __init__(self, root, ai_config_path=None):
        self.root = Path(root).resolve()
        self.state = self.root / '.latex-workspace'
        self.state.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.projects = {}
        self.jobs = {}
        self.active = {}
        self.processes = {}
        self.ai = AIAssistant(self, config_path=ai_config_path)
        self.comments = SentenceComments(self, APIError, write_json)
        self.completions = CompletionIndex(self, APIError)
        self.discover()

    def discover(self):
        with self.lock:
            candidates = [self.root] if list(self.root.glob('*.tex')) else []
            candidates += [p for p in sorted(self.root.iterdir()) if p.is_dir() and not p.name.startswith('.') and p.name not in IGNORED and next(p.rglob('*.tex'), None)]
            for path in candidates:
                relative = str(path.relative_to(self.root))
                key = digest(relative.encode())[:12]
                self.projects[key] = path
            return [self.project_info(key) for key in self.projects]

    def project(self, key):
        if key not in self.projects:
            raise APIError('프로젝트를 찾을 수 없습니다.', 404)
        return self.projects[key]

    def config_path(self, key):
        return self.state / 'projects' / key / 'config.json'

    def config(self, key):
        try:
            return json.loads(self.config_path(key).read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def project_info(self, key):
        root = self.project(key)
        mains = []
        for file in sorted(root.rglob('*.tex')):
            if any(part.startswith('.') for part in file.relative_to(root).parts):
                continue
            if file.is_symlink() or not file.resolve().is_relative_to(root):
                continue
            content = file.read_text(encoding='utf-8', errors='replace')
            if re.search(r'^\s*\\documentclass', content, re.M):
                mains.append(file.relative_to(root).as_posix())
        config = self.config(key)
        main = config.get('main')
        if main not in mains:
            main = 'main.tex' if 'main.tex' in mains else (mains[0] if mains else '')
        title = root.name.replace('_', ' ').strip()
        if main:
            text = (root / main).read_text(encoding='utf-8', errors='replace')
            match = re.search(r'^\s*\\title\{([^}]+)\}', text, re.M)
            if match:
                title = match.group(1).replace('--', '–').replace('\\', '').strip()
        latest = self.latest(key)
        return {'id': key, 'name': root.name, 'title': title, 'main': main, 'mains': mains,
                'engine': config.get('engine', 'pdflatex'), 'pdf_version': latest.get('version'),
                'compiled_at': latest.get('compiled_at'), 'active_job': self.active.get(key)}

    def safe_path(self, key, relative):
        root = self.project(key)
        if not isinstance(relative, str) or not relative or '\x00' in relative:
            raise APIError('파일 경로가 올바르지 않습니다.')
        parts = Path(relative).parts
        if Path(relative).is_absolute() or any(part in ('..', '.') or part.startswith('.') for part in parts):
            raise APIError('프로젝트 밖의 경로에는 접근할 수 없습니다.', 403)
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path == root:
            raise APIError('프로젝트 밖의 경로에는 접근할 수 없습니다.', 403)
        return path

    def tree(self, key):
        root = self.project(key)
        def walk(directory):
            items = []
            for path in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if path.name.startswith('.') or path.name in IGNORED or path.is_symlink():
                    continue
                if path.suffix in {'.aux', '.log', '.out', '.fls', '.fdb_latexmk', '.synctex', '.gz'}:
                    continue
                entry = {'name': path.name, 'path': path.relative_to(root).as_posix(), 'type': 'folder' if path.is_dir() else 'file'}
                if path.is_dir():
                    entry['children'] = walk(path)
                else:
                    entry['editable'] = path.suffix.lower() in TEXT_EXTENSIONS
                    entry['size'] = path.stat().st_size
                items.append(entry)
            return items
        return walk(root)

    def read_file(self, key, relative):
        path = self.safe_path(key, relative)
        if not path.is_file():
            raise APIError('파일을 찾을 수 없습니다.', 404)
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            raise APIError('이 파일은 텍스트 편집을 지원하지 않습니다.')
        data = path.read_bytes()
        if len(data) > 4 * 1024 * 1024:
            raise APIError('편집 가능한 파일 크기는 최대 4 MB입니다.', 413)
        try:
            content = data.decode('utf-8')
        except UnicodeDecodeError:
            raise APIError('UTF-8 텍스트 파일만 편집할 수 있습니다.')
        return {'path': relative, 'content': content, 'revision': digest(data)}

    def save_file(self, data):
        key, relative, content = data.get('project'), data.get('path'), data.get('content')
        if not isinstance(content, str):
            raise APIError('저장할 텍스트가 필요합니다.')
        with self.lock:
            previous = self.read_file(key, relative)
            if data.get('revision') != previous['revision']:
                raise APIError('다른 창이나 프로그램에서 파일이 변경되었습니다. 내용을 복사해 보관한 뒤 파일을 다시 열어 주세요.', 409)
            if previous['content'] == content:
                return {'revision': previous['revision'], 'saved_at': time.time()}
            encoded = content.encode('utf-8')
            if len(encoded) > 4 * 1024 * 1024:
                raise APIError('편집 가능한 파일 크기는 최대 4 MB입니다.', 413)
            path = self.safe_path(key, relative)
            backup = self.state / 'backups' / key / relative
            backup.mkdir(parents=True, exist_ok=True)
            (backup / f'{time.time_ns()}.bak').write_text(previous['content'], encoding='utf-8')
            for old in sorted(backup.glob('*.bak'))[:-30]:
                old.unlink()
            temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
            temp.write_bytes(encoded)
            temp.chmod(path.stat().st_mode)
            temp.replace(path)
            return {'revision': digest(encoded), 'saved_at': time.time()}

    def create_entry(self, data):
        key, relative = data.get('project'), data.get('path')
        path = self.safe_path(key, relative)
        with self.lock:
            if path.exists():
                raise APIError('같은 이름의 파일이나 폴더가 이미 있습니다.', 409)
            if data.get('kind') == 'folder':
                path.mkdir(parents=True)
            else:
                if path.suffix.lower() not in TEXT_EXTENSIONS:
                    raise APIError('.tex, .bib 등 텍스트 파일 확장자를 입력해 주세요.')
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('x', encoding='utf-8') as stream:
                    stream.write('% ' + path.stem + '\n' if path.suffix == '.tex' else '')
        return {'path': relative}

    def latest(self, key):
        try:
            return json.loads((self.state / 'projects' / key / 'latest.json').read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def pdf(self, key, version=None):
        self.project(key)
        version = version or self.latest(key).get('version')
        if not version:
            raise APIError('먼저 문서를 컴파일해 주세요.', 404)
        if not isinstance(version, str) or not re.fullmatch(r'[0-9a-f]{32}', version):
            raise APIError('PDF 버전이 올바르지 않습니다.')
        path = self.state / 'projects' / key / 'pdf' / (version + '.pdf')
        if not path.is_file():
            raise APIError('이 PDF 버전은 더 이상 보관되어 있지 않습니다. PDF를 새로고침하거나 다시 컴파일해 주세요.', 404)
        return path

    @staticmethod
    def source_snapshot(source, main):
        files = {}
        for path in source.rglob('*'):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in TEXT_EXTENSIONS:
                if path.stat().st_size <= 4 * 1024 * 1024:
                    files[path.relative_to(source).as_posix()] = path.read_text(encoding='utf-8', errors='replace')
        return {'source_root': str(source), 'main': main, 'files': files}

    @staticmethod
    def refine_included_source(snapshot, relative, index, hints):
        """TeX may tag a paragraph ending at EOF with its parent's input line."""
        def includes(content):
            names = re.findall(r'\\(?:input|include)\s*(?:\{([^}]+)\}|([^\s%{}]+))', content)
            for braced, bare in names:
                name = Path(braced or bare)
                if not name.suffix:
                    name = name.with_suffix('.tex')
                if name.as_posix() in snapshot['files']:
                    yield name.as_posix()
        anchor = snapshot['files'][relative].splitlines()[index]
        pending = list(includes(anchor))
        seen = set()
        while pending and len(seen) < 100:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            pending.extend(includes(snapshot['files'][name]))
        for hint in hints:
            hint = hint.strip().replace('\u00ad', '')[:1500]
            if len(hint) < 3:
                continue
            matches = [(name, row) for name in sorted(seen)
                       for row, content in enumerate(snapshot['files'][name].splitlines()) if hint in content]
            if len(matches) == 1:
                return matches[0]
        return relative, index

    def sync_snapshot(self, key, version=None):
        pdf = self.pdf(key, version)
        version = pdf.stem
        metadata = pdf.with_suffix('.sources.json')
        if metadata.is_file() and pdf.with_suffix('.synctex.gz').is_file():
            snapshot = json.loads(metadata.read_text(encoding='utf-8'))
        else:
            # Support PDFs generated before reverse synchronization was added.
            job = self.state / 'projects' / key / 'jobs' / version
            try:
                result = json.loads((job / 'result.json').read_text())
                source = job / 'source'
                output = job / 'output' / (Path(result['main']).stem + '.pdf')
                if not output.with_suffix('.synctex.gz').is_file() or not source.is_dir():
                    raise FileNotFoundError
                snapshot = self.source_snapshot(source, result['main'])
                pdf = output
            except (FileNotFoundError, KeyError, ValueError):
                raise APIError('이 PDF에는 소스 위치 정보가 없습니다. 한 번 다시 컴파일해 주세요.', 409)
        return pdf, snapshot

    def source_location(self, key, version, page, x, y, text='', word=''):
        """Resolve PDF big-point coordinates against that exact build's SyncTeX."""
        root = self.project(key)
        pdf = self.pdf(key, version)
        version = pdf.stem
        try:
            page, x, y = int(page), float(x), float(y)
            if not (1 <= page <= 100000 and all(math.isfinite(n) and 0 <= n <= 20000 for n in (x, y))):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise APIError('PDF 페이지 또는 클릭 좌표가 올바르지 않습니다.')
        if not shutil.which('synctex'):
            raise APIError('소스 위치를 찾으려면 TeX Live의 synctex 프로그램이 필요합니다.', 503)
        pdf, snapshot = self.sync_snapshot(key, version)
        env = os.environ.copy()
        # Never let SyncTeX launch an editor through an inherited environment setting.
        env.pop('SYNCTEX_EDITOR', None)
        try:
            result = subprocess.run(['synctex', 'edit', '-o', f'{page}:{x:.6f}:{y:.6f}:{pdf}'],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=8)
        except subprocess.TimeoutExpired:
            raise APIError('소스 위치 조회 시간이 초과되었습니다. 다시 시도해 주세요.', 504)
        source_root = Path(snapshot['source_root']).resolve()
        for match in re.finditer(r'^Input:(.+)\r?\nLine:(\d+)\r?\nColumn:(-?\d+)', result.stdout, re.M):
            path = Path(match[1].strip())
            candidate = (source_root / path).resolve()
            if not candidate.is_relative_to(source_root):
                continue
            relative = candidate.relative_to(source_root).as_posix()
            if relative not in snapshot['files']:
                continue
            before = snapshot['files'][relative].splitlines()
            if not before:
                continue
            index = min(max(0, int(match[2]) - 1), len(before) - 1)
            relative, index = self.refine_included_source(snapshot, relative, index, [text, word])
            before = snapshot['files'][relative].splitlines()
            try:
                live = self.read_file(key, relative)
            except APIError:
                continue
            compiled_line = index + 1
            after = live['content'].splitlines()
            if not before or not after:
                continue
            mapped = index
            changed = snapshot['files'][relative] != live['content']
            if changed:
                for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
                    if i1 <= index < i2:
                        mapped = j1 + (index - i1 if tag == 'equal' else min(index - i1, max(0, j2 - j1 - 1)))
                        break
            return {'project': key, 'version': version, 'path': relative,
                    'line': min(mapped, len(after) - 1) + 1, 'column': max(1, int(match[3])),
                    'compiled_line': compiled_line, 'line_text': before[index], 'source_changed': changed}
        raise APIError('이 위치에 연결된 프로젝트 소스를 찾지 못했습니다. 본문 텍스트를 더블클릭해 주세요.', 404)

    def pdf_location(self, data):
        key, relative = data.get('project'), data.get('path')
        self.safe_path(key, relative)
        live = self.read_file(key, relative)
        content = data.get('content', live['content'])
        if not isinstance(content, str) or len(content.encode('utf-8')) > 4 * 1024 * 1024:
            raise APIError('편집기 문맥은 4 MB 이내의 텍스트여야 합니다.')
        line, column = data.get('line'), data.get('column', 1)
        lines = content.split('\n')
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (line, column)) or not 1 <= line <= len(lines) or not 1 <= column <= len(lines[line - 1].encode('utf-16-le')) // 2 + 1:
            raise APIError('소스 행 또는 열 번호가 올바르지 않습니다.')
        if not shutil.which('synctex'):
            raise APIError('PDF 위치를 찾으려면 TeX Live의 synctex 프로그램이 필요합니다.', 503)
        version = self.pdf(key, data.get('version')).stem
        pdf, snapshot = self.sync_snapshot(key, version)
        if relative not in snapshot['files']:
            raise APIError('이 파일이 PDF에 아직 반영되지 않았습니다. 다시 컴파일해 주세요.', 409)
        compiled = snapshot['files'][relative].split('\n')
        index = line - 1
        if content != snapshot['files'][relative]:
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, lines, compiled, autojunk=False).get_opcodes():
                if i1 <= index < i2:
                    if tag != 'equal':
                        raise APIError('이 문장이 PDF에 아직 반영되지 않았습니다. 다시 컴파일한 뒤 이동해 주세요.', 409)
                    index = j1 + index - i1
                    break
        if not compiled[index].strip() or compiled[index].lstrip().startswith('%'):
            raise APIError('빈 줄이나 LaTeX 주석에는 대응하는 PDF 본문이 없습니다.', 404)
        source = (Path(snapshot['source_root']) / relative).resolve()
        env = os.environ.copy()
        env.pop('SYNCTEX_VIEWER', None)
        try:
            result = subprocess.run(['synctex', 'view', '-i', f'{index + 1}:{column}:{source}', '-o', str(pdf)],
                                    cwd=self.project(key), env=env, capture_output=True, text=True, timeout=8)
        except subprocess.TimeoutExpired:
            raise APIError('PDF 위치 조회 시간이 초과되었습니다.', 504)
        for record in re.split(r'(?=^Page:)', result.stdout, flags=re.M)[1:]:
            try:
                values = dict(re.findall(r'^(Page|x|y|h|v|W|H):([^\r\n]+)', record, re.M))
                page = int(values['Page'])
                x, y, h, v, width, height = [float(values[name]) for name in ('x', 'y', 'h', 'v', 'W', 'H')]
                if page < 1 or not all(math.isfinite(n) and abs(n) <= 20000 for n in (x, y, h, v, width, height)):
                    continue
            except (KeyError, ValueError):
                continue
            return {'project': key, 'version': version, 'path': relative, 'line': line,
                    'compiled_line': index + 1, 'source_changed': content != snapshot['files'][relative],
                    'page': page, 'x': x, 'y': y, 'box': {'x': h, 'y': v - height, 'width': max(1, width), 'height': max(5, height)}}
        raise APIError('이 소스에 대응하는 PDF 위치가 없습니다. 본문 문장을 선택하거나 다시 컴파일해 주세요.', 404)

    def start_compile(self, data):
        key = data.get('project')
        root = self.project(key)
        main = data.get('main') or self.project_info(key)['main']
        engine = data.get('engine', 'pdflatex')
        if engine not in ENGINES:
            raise APIError('지원하지 않는 컴파일러입니다.')
        if not shutil.which('latexmk') or not shutil.which(engine):
            raise APIError(f'{engine}와 latexmk가 필요합니다. TeX Live를 설치해 주세요.', 503)
        path = self.safe_path(key, main)
        if path.suffix != '.tex' or not path.is_file():
            raise APIError('컴파일할 메인 .tex 파일을 선택해 주세요.')
        with self.lock:
            if key in self.active:
                return dict(self.jobs[self.active[key]])
            job_id = uuid.uuid4().hex
            job = {'id': job_id, 'project': key, 'status': 'running', 'main': main, 'engine': engine,
                   'started_at': time.time(), 'log': '', 'errors': [], 'warnings': []}
            self.jobs[job_id] = job
            self.active[key] = job_id
            write_json(self.config_path(key), {'main': main, 'engine': engine})
            threading.Thread(target=self.compile, args=(job_id, root), daemon=True).start()
            return dict(job)

    @staticmethod
    def diagnostics(log, source):
        errors, warnings, seen = [], [], set()
        for line in log.splitlines():
            match = re.match(r'(.+?\.(?:tex|sty|cls)):(\d+):\s*(.+)', line)
            if match and '==>' not in match[3]:
                file = match[1].replace(str(source) + '/', '').removeprefix('./')
                item = {'file': file, 'line': int(match[2]), 'message': match[3]}
                key = (file, item['line'], item['message'])
                if key not in seen:
                    errors.append(item)
                    seen.add(key)
            elif line.startswith('! '):
                if line not in seen:
                    errors.append({'file': None, 'line': None, 'message': line[2:]})
                    seen.add(line)
            elif ('Warning:' in line or line.startswith('Overfull \\hbox')) and line not in seen:
                warnings.append({'message': line})
                seen.add(line)
        return errors[:80], warnings[:100]

    def compile(self, job_id, root):
        job = self.jobs[job_id]
        key = job['project']
        directory = self.state / 'projects' / key / 'jobs' / job_id
        source, output = directory / 'source', directory / 'output'
        try:
            # Compile a snapshot so a concurrent save cannot change a running build.
            with self.lock:
                shutil.copytree(root, source, ignore=shutil.ignore_patterns('.*', '__pycache__', 'node_modules', 'latex-workspace', '*.aux', '*.log', '*.fls', '*.fdb_latexmk'), symlinks=True)
            # Do not follow linked project files during compilation.
            for file in source.rglob('*'):
                if file.is_symlink():
                    file.unlink()
            output.mkdir(parents=True)
            env = os.environ.copy()
            texmf = self.state / 'texmf'
            env['TEXMFHOME'] = str(texmf)
            env['TEXMFCONFIG'] = str(self.state / 'texmf-config')
            env['TEXMFVAR'] = str(self.state / 'texmf-var')
            env['TEXINPUTS'] = str(texmf / 'tex') + '//:'
            env['TFMFONTS'] = str(texmf / 'fonts/tfm') + '//:'
            env['VFFONTS'] = str(texmf / 'fonts/vf') + '//:'
            env['T1FONTS'] = str(texmf / 'fonts/type1') + '//:'
            env['ENCFONTS'] = str(texmf / 'fonts/enc') + '//:'
            env['TEXFONTMAPS'] = str(texmf / 'fonts/map') + '//:'
            command = ['latexmk', '-norc', ENGINES[job['engine']], '-synctex=1', '-interaction=nonstopmode',
                       '-file-line-error', '-no-shell-escape', f'-outdir={output}', job['main']]
            logfile = directory / 'compiler.log'
            with logfile.open('w') as stream:
                process = subprocess.Popen(command, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                with self.lock:
                    self.processes[job_id] = process
                    if job.get('cancel_requested'):
                        os.killpg(process.pid, signal.SIGTERM)
                try:
                    code = process.wait(timeout=240)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait()
                    code = -1
                    stream.write('\n컴파일 제한 시간(240초)을 초과했습니다.\n')
            log = logfile.read_text(errors='replace')
            errors, warnings = self.diagnostics(log, source)
            pdf = output / (Path(job['main']).stem + '.pdf')
            final_log = output / (Path(job['main']).stem + '.log')
            if code == 0 and final_log.is_file():
                # Early LaTeX passes contain references that later passes resolve.
                errors, warnings = self.diagnostics(final_log.read_text(errors='replace'), source)
            with self.lock:
                cancelled = job.get('cancel_requested', False)
                success = code == 0 and pdf.is_file() and not cancelled
                if success:
                    published = self.state / 'projects' / key / 'pdf' / (job_id + '.pdf')
                    published.parent.mkdir(exist_ok=True)
                    shutil.copyfile(pdf, published)
                    synctex = pdf.with_suffix('.synctex.gz')
                    if synctex.is_file():
                        shutil.copyfile(synctex, published.with_suffix('.synctex.gz'))
                        write_json(published.with_suffix('.sources.json'), self.source_snapshot(source, job['main']))
                    write_json(published.parent.parent / 'latest.json', {'version': job_id, 'compiled_at': time.time(), 'main': job['main']})
                    job['pdf_version'] = job_id
                job.update(status='cancelled' if cancelled else ('success' if success else 'error'),
                           log=log[-180000:], errors=errors, warnings=warnings, finished_at=time.time())
                job['duration'] = round(job['finished_at'] - job['started_at'], 1)
                if not success and not errors and not cancelled:
                    job['errors'] = [{'file': None, 'line': None, 'message': '컴파일에 실패했습니다. 전체 로그에서 원인을 확인해 주세요.'}]
                write_json(directory / 'result.json', job)
        except Exception as exc:
            with self.lock:
                job.update(status='error', log=str(exc), errors=[{'file': None, 'line': None, 'message': str(exc)}], finished_at=time.time())
        finally:
            with self.lock:
                self.active.pop(key, None)
                self.processes.pop(job_id, None)
            # Keep the latest builds and PDF versions; backups are independent.
            jobs_dir = directory.parent
            for old in sorted(jobs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[4:]:
                shutil.rmtree(old, ignore_errors=True)
            pdf_dir = self.state / 'projects' / key / 'pdf'
            if pdf_dir.exists():
                for old in sorted(pdf_dir.glob('*.pdf'), key=lambda p: p.stat().st_mtime, reverse=True)[4:]:
                    old.unlink(missing_ok=True)
                    old.with_suffix('.synctex.gz').unlink(missing_ok=True)
                    old.with_suffix('.sources.json').unlink(missing_ok=True)

    def job_status(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise APIError('컴파일 작업을 찾을 수 없습니다.', 404)
            result = dict(self.jobs[job_id])
            if result['status'] == 'running':
                logfile = self.state / 'projects' / result['project'] / 'jobs' / job_id / 'compiler.log'
                if logfile.exists():
                    result['log'] = logfile.read_text(errors='replace')[-180000:]
            return result

    def cancel(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise APIError('컴파일 작업을 찾을 수 없습니다.', 404)
            if self.jobs[job_id]['status'] == 'running':
                self.jobs[job_id]['cancel_requested'] = True
                process = self.processes.get(job_id)
                if process and process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
            return {'ok': True}


class Handler(BaseHTTPRequestHandler):
    server_version = 'LaTeXWorkspace/1.0'

    @property
    def workspace(self):
        return self.server.workspace

    def log_message(self, format, *args):
        if args and str(args[1]) not in ('200', '304'):
            super().log_message(format, *args)

    def respond(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type=None, download=False):
        if not path.is_file():
            raise APIError('파일을 찾을 수 없습니다.', 404)
        data = path.read_bytes()
        mime = content_type or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-cache')
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="document.pdf"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            url = urlparse(self.path)
            query = {key: value[0] for key, value in parse_qs(url.query).items()}
            route = url.path
            if route == '/api/projects':
                self.respond({'projects': self.workspace.discover(), 'engines': [e for e in ENGINES if shutil.which(e)], 'workspace': str(self.workspace.root)})
            elif route == '/api/tree':
                self.respond({'tree': self.workspace.tree(query.get('project'))})
            elif route == '/api/file':
                self.respond(self.workspace.read_file(query.get('project'), query.get('path')))
            elif route == '/api/job':
                self.respond(self.workspace.job_status(query.get('id')))
            elif route == '/api/pdf':
                self.send_file(self.workspace.pdf(query.get('project'), query.get('v')), 'application/pdf', query.get('download') == '1')
            elif route == '/api/source-location':
                self.respond(self.workspace.source_location(query.get('project'), query.get('version'), query.get('page'), query.get('x'), query.get('y'), query.get('text', ''), query.get('word', '')))
            elif route == '/api/comments':
                self.respond({'comments': self.workspace.comments.list(query.get('project'))})
            elif route == '/api/ai/config':
                self.respond(self.workspace.ai.settings())
            elif route == '/api/ai/session':
                self.respond(self.workspace.ai.session(query.get('project'), query.get('session')))
            elif route == '/api/ai/run':
                self.respond(self.workspace.ai.get_run(query.get('id')))
            elif route == '/api/ai/stream':
                self.send_ai_stream(query.get('id'))
            elif route == '/api/asset':
                path = self.workspace.safe_path(query.get('project'), query.get('path'))
                if path.suffix.lower() not in {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif'}:
                    raise APIError('미리보기를 지원하지 않는 형식입니다.')
                self.send_file(path)
            elif route == '/api/latest':
                self.workspace.project(query.get('project'))
                self.respond(self.workspace.latest(query.get('project')))
            elif route in ('/', '/viewer'):
                self.send_file(APP / 'static' / ('index.html' if route == '/' else 'viewer.html'), 'text/html; charset=utf-8')
            else:
                path = (APP / 'static' / route.lstrip('/')).resolve()
                if not path.is_relative_to(APP / 'static'):
                    raise APIError('허용되지 않는 경로입니다.', 403)
                self.send_file(path, 'text/javascript; charset=utf-8' if path.suffix in ('.js', '.mjs') else None)
        except (APIError, AssistantError) as exc:
            self.respond({'error': exc.message}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.respond({'error': str(exc)}, 500)

    def send_ai_stream(self, run_id):
        self.workspace.ai.get_run(run_id)  # Validate before sending streaming headers.
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-transform')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            for snapshot in self.workspace.ai.stream_updates(run_id):
                if snapshot is None:
                    self.wfile.write(b': keep-alive\n\n')
                else:
                    data = json.dumps(snapshot, ensure_ascii=False)
                    self.wfile.write(f'id: {snapshot["revision"]}\nevent: update\ndata: {data}\n\n'.encode())
                self.wfile.flush()
        except OSError:
            pass

    def do_POST(self):
        self.mutate()

    def do_PUT(self):
        self.mutate()

    def do_DELETE(self):
        self.mutate()

    def mutate(self):
        try:
            # Browser requests must come from this workspace, preventing cross-site writes.
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                raise APIError('다른 사이트의 저장 요청은 허용되지 않습니다.', 403)
            if 'application/json' not in self.headers.get('Content-Type', ''):
                raise APIError('JSON 요청이 필요합니다.', 415)
            size = int(self.headers.get('Content-Length', 0))
            if size <= 0 or size > 8 * 1024 * 1024:
                raise APIError('요청 크기가 올바르지 않습니다.', 413)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise APIError('JSON 객체가 필요합니다.')
            route = urlparse(self.path).path
            if route == '/api/file' and self.command == 'PUT':
                result = self.workspace.save_file(data)
            elif route == '/api/entry' and self.command == 'POST':
                result = self.workspace.create_entry(data)
            elif route == '/api/pdf-location' and self.command == 'POST':
                result = self.workspace.pdf_location(data)
            elif route == '/api/completions' and self.command == 'POST':
                result = self.workspace.completions.collect(data.get('project'), data.get('buffers'))
            elif route == '/api/comments' and self.command in ('POST', 'PUT', 'DELETE'):
                result = self.workspace.comments.mutate(data, self.command)
            elif route == '/api/compile' and self.command == 'POST':
                result = self.workspace.start_compile(data)
            elif route == '/api/cancel' and self.command == 'POST':
                result = self.workspace.cancel(data.get('id'))
            elif route == '/api/ai/chat' and self.command == 'POST':
                result = self.workspace.ai.start(data)
            elif route == '/api/ai/config' and self.command == 'PUT':
                result = self.workspace.ai.update_config(data)
            elif route == '/api/ai/cancel' and self.command == 'POST':
                result = self.workspace.ai.cancel(data.get('id'))
            elif route == '/api/ai/proposal' and self.command == 'POST':
                result = self.workspace.ai.proposal_status(data)
            else:
                raise APIError('요청을 찾을 수 없습니다.', 404)
            self.respond(result)
        except (APIError, AssistantError) as exc:
            self.respond({'error': exc.message}, exc.status)
        except (ValueError, TypeError):
            self.respond({'error': '요청 형식이 올바르지 않습니다.'}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.respond({'error': str(exc)}, 500)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=APP.parent, help='논문 프로젝트를 포함하는 폴더')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--ai-config', type=Path, default=APP / 'ai_config.json')
    parser.add_argument('--ai-port', type=int)
    parser.add_argument('--ai-model')
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.workspace = Workspace(args.root, ai_config_path=args.ai_config)
    if args.ai_port is not None or args.ai_model is not None:
        overrides = {}
        if args.ai_port is not None:
            overrides['port'] = args.ai_port
        if args.ai_model is not None:
            overrides['model'] = args.ai_model
        server.workspace.ai.update_config(overrides)
    print(f'LaTeX Workspace → http://{args.host}:{args.port}', flush=True)
    print(f'Projects: {args.root.resolve()}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for process in list(server.workspace.processes.values()):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        server.server_close()


if __name__ == '__main__':
    main()

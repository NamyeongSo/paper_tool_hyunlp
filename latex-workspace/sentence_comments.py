"""Project-local sentence annotations, separate from manuscript sources."""
import json
import re
import time
import uuid


def position_offset(content, position):
    """Convert CodeMirror's UTF-16 line/ch coordinates to Python string offsets."""
    if not isinstance(position, dict):
        raise ValueError
    line, ch = position.get('line'), position.get('ch')
    lines = content.split('\n')
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (line, ch)) or line >= len(lines):
        raise ValueError
    units = lines[line].encode('utf-16-le')
    if ch * 2 > len(units):
        raise ValueError
    prefix = units[:ch * 2].decode('utf-16-le')
    return sum(len(item) + 1 for item in lines[:line]) + len(prefix)


def offset_position(content, offset):
    prefix = content[:offset]
    return {'line': prefix.count('\n'), 'ch': len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


class SentenceComments:
    def __init__(self, workspace, error, write_json):
        self.workspace, self.error, self.write_json = workspace, error, write_json

    def path(self, project):
        self.workspace.project(project)
        return self.workspace.state / 'projects' / project / 'comments.json'

    def read(self, project):
        try:
            return json.loads(self.path(project).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []

    @staticmethod
    def locate(comment, content):
        quote, before, after = comment['quote'], comment['before'], comment['after']
        positions = []
        start = 0
        while len(positions) <= 2000:
            at = content.find(quote, start)
            if at < 0:
                break
            score = int(bool(before) and content[max(0, at - len(before)):at] == before) + int(bool(after) and content[at + len(quote):at + len(quote) + len(after)] == after)
            positions.append((score, at))
            start = at + 1
        if not positions:
            return None
        positions.sort(reverse=True)
        if len(positions) > 1 and positions[0][0] == positions[1][0]:
            # Do not silently attach a comment to a different repeated sentence.
            return None
        at = positions[0][1]
        return {'from': offset_position(content, at), 'to': offset_position(content, at + len(quote))}

    def list(self, project):
        with self.workspace.lock:
            records = self.read(project)
            files, result = {}, []
            for comment in records:
                relative = comment['path']
                if relative not in files:
                    try:
                        files[relative] = self.workspace.read_file(project, relative)['content']
                    except self.error:
                        files[relative] = None
                content = files[relative]
                anchor = self.locate(comment, content) if content is not None else None
                result.append({key: value for key, value in comment.items() if key not in ('before', 'after')})
                result[-1].update(anchor=anchor, anchor_state='attached' if anchor else 'changed' if content is not None else 'missing')
            return result

    def mutate(self, data, method):
        project = data.get('project')
        with self.workspace.lock:
            records = self.read(project)
            if method == 'POST':
                if len(records) >= 2000:
                    raise self.error('프로젝트당 코멘트는 최대 2,000개까지 보관할 수 있습니다.')
                relative = data.get('path')
                file = self.workspace.read_file(project, relative)
                if data.get('source_revision') != file['revision']:
                    raise self.error('원문이 변경되었습니다. 최신 문장을 다시 선택해 주세요.', 409)
                try:
                    start, end = [position_offset(file['content'], data.get(key)) for key in ('from', 'to')]
                except (ValueError, UnicodeError, TypeError):
                    raise self.error('코멘트를 달 문장 범위가 올바르지 않습니다.')
                if start >= end or not file['content'][start:end].strip() or end - start > 12000:
                    raise self.error('1~12,000자 이내의 문장을 선택해 주세요.')
                quote = file['content'][start:end]
                if data.get('quote') != quote:
                    raise self.error('선택한 문장과 원문이 일치하지 않습니다. 다시 선택해 주세요.', 409)
                comment = {'id': uuid.uuid4().hex, 'path': relative, 'quote': quote,
                           'before': file['content'][max(0, start - 160):start], 'after': file['content'][end:end + 160],
                           'line': data['from']['line'] + 1, 'created_at': time.time(), 'revision': 0, 'resolved': False}
                records.append(comment)
            else:
                cid = data.get('id')
                if not isinstance(cid, str) or not re.fullmatch('[0-9a-f]{32}', cid):
                    raise self.error('코멘트 ID가 올바르지 않습니다.')
                comment = next((item for item in records if item['id'] == cid), None)
                if not comment:
                    raise self.error('코멘트를 찾을 수 없습니다.', 404)
                if isinstance(data.get('revision'), bool) or not isinstance(data.get('revision'), int) or data.get('revision') != comment['revision']:
                    raise self.error('다른 창에서 코멘트가 변경되었습니다. 새로고침 후 다시 시도해 주세요.', 409)
            if method == 'DELETE':
                records.remove(comment)
            else:
                text = data.get('text', comment.get('text', ''))
                resolved = data.get('resolved', comment['resolved'])
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 6000:
                    raise self.error('코멘트는 1~6,000자로 입력해 주세요.')
                if not isinstance(resolved, bool):
                    raise self.error('해결 여부는 true 또는 false여야 합니다.')
                comment.update(text=text.strip(), resolved=resolved, updated_at=time.time(), revision=comment['revision'] + 1)
            self.write_json(self.path(project), records)
            return {'comments': self.list(project)}

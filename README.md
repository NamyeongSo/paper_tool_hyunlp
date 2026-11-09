# Folio · LaTeX Workspace

LaTeX 소스를 편집하고 컴파일한 PDF를 확인하는 한국어 로컬 웹앱입니다.
문장별 코멘트와 AI 교정 도우미를 제공합니다. 서버와 편집기 실행에는 Python 표준 라이브러리만 사용합니다.

이 저장소에는 앱 코드, 테스트, 외부 라이브러리, 직접 작성한 합성 데모만 포함되어 있습니다.
API 키, 실제 논문, 개인 설정, 채팅 기록, 코멘트 데이터, 백업, 생성된 PDF, 개인 화면 캡처는 포함하지 않습니다.

## 실행

Python 3.10 이상이 필요합니다. PDF 컴파일에는 TeX Live의 `latexmk`와 `pdflatex`가 필요하고,
소스↔PDF 위치 이동에는 `synctex`가 필요합니다. LuaLaTeX와 XeLaTeX도 설치되어 있으면 선택할 수 있습니다.

```bash
git clone https://github.com/NamyeongSo/paper_write_hyunlp.git
cd paper_write_hyunlp
python latex-workspace/server.py
```

브라우저에서 **http://127.0.0.1:8765** 를 엽니다.
기본 작업 폴더는 저장소 루트이며, 포함된 `demo-paper`를 열어 기능을 살펴볼 수 있습니다.
처음 실행하면 데모 문서를 컴파일합니다. AI 키를 입력하지 않아도 편집·컴파일·코멘트 기능을 사용할 수 있습니다.

자신의 논문은 별도의 폴더에서 여는 것을 권장합니다.

```bash
python latex-workspace/server.py --root /path/to/papers --port 8765
```

작업 폴더 바로 아래에 `.tex` 파일이 있거나, 하위 프로젝트 폴더에 `.tex` 파일이 있으면 자동으로 찾습니다.
기본 바인딩은 `127.0.0.1`입니다. 원격 서버에서는 SSH나 VS Code의 포트 포워딩으로 접속합니다.

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

## 편집과 PDF

- 파일 트리에서 파일을 열고 탭으로 전환합니다. 편집 내용은 약 0.9초 뒤 실제 파일에 자동 저장됩니다. `Ctrl/⌘ + S`로 즉시 저장할 수 있습니다.
- 컴파일 버튼 또는 `Ctrl/⌘ + Enter`로 설정된 메인 문서 전체를 컴파일합니다. 실행 중 같은 버튼을 누르면 중지합니다.
- PDF 확대·페이지 이동·다운로드·별도 창 열기를 지원합니다. 별도 PDF 창은 컴파일 결과를 자동으로 반영합니다.
- **소스의 문장을 더블클릭**하면 해당 PDF 위치로 이동하고 잠시 강조합니다. 편집기 아래 **PDF로 이동 ↗** 버튼도 사용할 수 있습니다.
- **PDF 본문을 더블클릭**하면 해당 소스 파일과 줄로 이동합니다. 확대 비율과 HiDPI에서도 동작합니다.
- 마지막 컴파일 이후 삽입된 줄은 보정합니다. 새로 바뀐 문장이 PDF에 없으면 다시 컴파일해야 합니다. 빈 줄, 주석, 출력되지 않는 정의는 PDF 위치가 없을 수 있습니다.
- 컴파일 오류에서 파일·줄 번호를 클릭해 이동할 수 있습니다. 실패하더라도 이전의 정상 PDF를 유지합니다.
- 파일 트리·편집기·PDF 사이의 경계를 끌어 너비를 조절합니다. 작은 화면에서는 소스와 PDF를 위아래로 배치합니다.

## 문장 코멘트

1. 문장을 선택하고 **코멘트 +** 또는 `Ctrl/⌘ + Alt + M`을 누릅니다. 선택이 없으면 커서가 있는 문장을 잡습니다.
2. 인용문을 확인하고 코멘트를 저장합니다. 원문을 먼저 일반 자동 저장 절차로 저장합니다.
3. **코멘트 N** 버튼으로 파일 목록과 LaTeX 편집기 사이의 패널을 켜고 끕니다. 표시 상태는 새로고침 후에도 유지됩니다.
4. 목록에서 수정·해결·다시 열기·삭제할 수 있습니다. **현재 파일만**을 해제하면 프로젝트 전체를 볼 수 있습니다.

코멘트가 달린 원문에는 노란 밑줄이 생깁니다. 밑줄을 누르면 코멘트가 열리고,
목록의 인용문을 누르면 편집기에서 원문을 선택합니다. 해결된 항목은 체크박스로 표시합니다.
작은 화면에서는 코멘트 패널을 접을 수 있는 오버레이로 표시합니다.

코멘트는 LaTeX 원문에 삽입하지 않고 서버의 별도 JSON에 저장합니다.
원문이 수정·삭제되거나 위치가 모호하면 **원문 위치 확인 필요**로 표시하며 원래 인용문과 코멘트를 유지합니다.

## AI 도우미

편집기 아래 **AI assistant → 설정(톱니바퀴)**에서 제공자와 API 키를 지정합니다.
현재 파일의 저장 전 편집 내용, 커서, 선택 영역과 관련 대화가 선택한 제공자로 전달됩니다.
수정안은 원문·수정문·이유를 확인한 뒤 **편집기에 적용**을 눌러 반영합니다.
파일 읽기·검색·수정안 제안 도구를 사용하는 ReAct 방식이며 모델이 원문을 직접 덮어쓰지 않습니다.

### EXAONE

기본 제공자는 **EXAONE · K-EXAONE 2.5**입니다. 설정창의 **EXAONE API 키**에 입력하고
**저장하고 연결 확인**을 누릅니다.

- URL: `https://api.exaone.ai/v1`
- 모델: `k-exaone_v2.5`
- 방식: Chat Completions, 스트리밍 지원
- 추론 강도: `low`, `high`, `max`. 항상 추론하며 추론과 답변 모두 출력 한도에 포함됩니다.

[EXAONE 공식 API 문서](https://docs.exaone.ai/ko/docs/k-exaone-2.5)를 참고했습니다.
저장 시 최대 16개 출력 토큰의 짧은 API 요청으로 연결을 확인합니다.
설정 조회나 새로고침은 추가 생성 요청을 보내지 않습니다.
서버 재시작 후에는 연결 확인 상태가 초기화되지만 저장한 키는 유지됩니다.

### OpenAI / 로컬 vLLM

제공자 목록에서 **OpenAI** 또는 **Localhost · vLLM**으로 전환할 수 있습니다.
제공자별 모델 이름과 API 키는 따로 관리합니다.
OpenAI는 Responses API, 로컬 vLLM은 Chat Completions를 사용합니다.
로컬 모델 이름과 포트는 실행 중인 추론 서버에 맞게 지정하세요.

스트리밍·중지·대화 복원·자동 문맥 압축을 지원합니다.
입력 길이 조회가 지원되지 않는 API에서는 보수적인 UTF-8 바이트 상한으로 추정합니다.
EXAONE의 문맥 길이는 524,288 토큰 이내로 제한합니다.

### 설정과 키 보관

- `latex-workspace/ai_config.example.json`: 비밀 값이 없는 배포용 기본값.
- `latex-workspace/ai_config.json`: 첫 설정 조회 시 예제에서 자동 생성되는 개인 설정. Git에서 제외합니다.
- `latex-workspace/ai_system_prompt.txt`: 교정 원칙. 요청 때마다 다시 읽습니다.
- `EXAONE_API_KEY`, `OPENAI_API_KEY`: 서버 환경변수로도 키를 설정할 수 있습니다. 웹에서 저장한 키가 우선합니다.

키는 작업 폴더의 `.latex-workspace/ai/.exaone-api-key` 또는 `.openai-api-key`에 권한 `0600`으로 보관합니다.
키 값을 설정 조회 응답·채팅 기록·브라우저 저장소에 넣지 않습니다.
설정창에서 새 키로 교체하거나 저장된 키를 삭제할 수 있습니다.
삭제 후 서버 환경변수에 키가 있으면 그 키를 사용합니다.
추론 원문은 표시하거나 대화 기록에 저장하지 않습니다.

## 개인 데이터와 백업

각 작업 폴더의 `.latex-workspace/`에는 다음 데이터가 생깁니다.

- 변경 전 원문 백업, 파일별 최대 30개
- 프로젝트별 컴파일 설정, 최근 빌드·PDF·SyncTeX·소스 스냅샷
- 문장 코멘트와 AI 대화·수정안·요약
- API 키와 선택적으로 설치한 TeX 패키지

이 폴더 전체를 개인 데이터로 취급하고 커밋하지 마세요. 기본 `.gitignore`에서 제외합니다.
실제 원문을 보관하는 작업 폴더와 `.latex-workspace/`를 함께 백업하면 됩니다.
다른 프로그램의 변경을 감지하면 덮어쓰기를 중단하고 현재 편집본 다운로드·디스크 파일 다시 열기를 안내합니다.

이 앱은 인증 기능이 없는 개인 로컬 편집기입니다. 루프백 또는 SSH 포워딩으로 사용하세요.
공유 서버 운영, 사용자 계정, 실시간 공동 편집은 제공하지 않습니다.

## 의존성과 검증

CodeMirror와 PDF.js를 라이선스와 함께 포함하므로 편집 중 CDN에 연결할 필요가 없습니다.
브라우저 자산을 다시 받거나 선택적 TeX 패키지를 설치하려면 다음을 실행합니다.

```bash
python latex-workspace/setup_dependencies.py --root /path/to/papers
```

서버 및 통합 테스트는 표준 라이브러리 `unittest`를 사용합니다.
실제 컴파일 관련 테스트에는 TeX Live와 Poppler의 `pdftotext`가 필요합니다.

```bash
python -m unittest discover -s latex-workspace/tests -v
```

브라우저 테스트는 임시 문서와 로컬 모의 모델 API를 사용합니다. 실제 API 키와 논문은 사용하지 않습니다.

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python latex-workspace/tests/browser_check.py
python latex-workspace/tests/browser_ai_check.py
python latex-workspace/tests/browser_provider_check.py
python latex-workspace/tests/browser_exaone_check.py
python latex-workspace/tests/browser_annotations_check.py
```

설치된 Chromium 경로는 `FOLIO_BROWSER` 환경변수로 지정할 수 있습니다.
생성된 스크린샷과 테스트 산출물도 Git에서 제외합니다.

[외부 구성 요소 및 라이선스](VENDOR_NOTICES.md)

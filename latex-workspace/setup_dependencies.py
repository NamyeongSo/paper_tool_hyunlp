#!/usr/bin/env python3
"""Restore the pinned browser assets and optional workspace-local TeX packages."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

APP = Path(__file__).resolve().parent
PACKAGES = {
    'codemirror': ('5.65.16', ['lib/codemirror.js', 'lib/codemirror.css', 'mode/stex/stex.js',
        'addon/edit/matchbrackets.js', 'addon/edit/closebrackets.js', 'addon/search/searchcursor.js',
        'addon/search/search.js', 'addon/dialog/dialog.js', 'addon/dialog/dialog.css', 'LICENSE']),
    'pdfjs-dist': ('4.10.38', ['build/pdf.mjs', 'build/pdf.worker.mjs', 'LICENSE']),
}


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def browser_assets():
    for name, (version, files) in PACKAGES.items():
        destination = APP / 'static/vendor' / name
        if all((destination / file).is_file() for file in files) and (name != 'pdfjs-dist' or (destination / 'standard_fonts').is_dir()):
            print(f'{name} {version}: ready', flush=True)
            continue
        metadata = json.loads(download(f'https://registry.npmjs.org/{name}/{version}'))
        payload = download(metadata['dist']['tarball'])
        algorithm, expected = metadata['dist']['integrity'].split('-', 1)
        actual = base64.b64encode(hashlib.new(algorithm, payload).digest()).decode()
        if actual != expected:
            raise RuntimeError(f'Integrity check failed: {name}')
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
            for member in archive.getmembers():
                relative = member.name.removeprefix('package/')
                if not member.isfile() or (relative not in files and not (name == 'pdfjs-dist' and relative.startswith('standard_fonts/'))):
                    continue
                target = (destination / relative).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise RuntimeError('Unsafe package path')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
        print(f'{name} {version}: installed', flush=True)


def tex_packages(root):
    state = root / '.latex-workspace'
    texmf = state / 'texmf'
    for package, style in [('inconsolata', 'inconsolata.sty'), ('algorithms', 'algorithm.sty'), ('algorithmicx', 'algpseudocode.sty')]:
        if (texmf / 'tex/latex/extra' / style).is_file() and (package != 'inconsolata' or (texmf / 'tex/latex/extra/t1zi4.fd').is_file()):
            print(f'{package}: ready', flush=True)
            continue
        location = f'fonts/{package}.zip' if package == 'inconsolata' else f'macros/latex/contrib/{package}.zip'
        payload = download('https://mirrors.ctan.org/' + location)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                filename = Path(member.filename).name
                extension = Path(filename).suffix
                folder = {'.sty': 'tex/latex/extra', '.fd': 'tex/latex/extra', '.tfm': 'fonts/tfm/extra', '.vf': 'fonts/vf/extra',
                          '.pfb': 'fonts/type1/extra', '.enc': 'fonts/enc/dvips/extra',
                          '.map': 'fonts/map/dvips/extra', '.ins': 'sources', '.dtx': 'sources'}.get(extension)
                if not folder and (filename.lower().startswith(('readme', 'license', 'copying')) or extension in ('.txt', '.md')):
                    folder = 'doc/' + package
                if folder:
                    target = texmf / folder / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
        if package == 'algorithms':
            sources = texmf / 'sources'
            subprocess.run(['latex', '-interaction=batchmode', 'algorithms.ins'], cwd=sources, check=True, stdout=subprocess.DEVNULL)
            target = texmf / 'tex/latex/extra'
            target.mkdir(parents=True, exist_ok=True)
            for file in sources.glob('*.sty'):
                shutil.copyfile(file, target / file.name)
        print(f'{package}: installed', flush=True)
    fontmap = state / 'texmf-var/fonts/map/pdftex/updmap/pdftex.map'
    if not fontmap.is_file():
        env = os.environ.copy()
        env.update(TEXMFHOME=str(texmf), TEXMFCONFIG=str(state / 'texmf-config'), TEXMFVAR=str(state / 'texmf-var'))
        result = subprocess.run(['updmap-user', '--enable', 'Map=zi4.map'], env=env, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        print('Workspace-local font map: installed', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=APP.parent)
    args = parser.parse_args()
    for command in ['latex', 'latexmk', 'pdflatex', 'updmap-user']:
        if not shutil.which(command):
            raise SystemExit(f'{command} is missing. Install TeX Live first.')
    browser_assets()
    tex_packages(args.root.resolve())
    print('Ready. Start with: python latex-workspace/server.py')


if __name__ == '__main__':
    main()

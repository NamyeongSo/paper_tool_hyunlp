# Third-party components

- **CodeMirror 5.65.16** — MIT. Source: https://github.com/codemirror/codemirror5. Included license: `latex-workspace/static/vendor/codemirror/LICENSE`.
- **PDF.js / pdfjs-dist 4.10.38** — Apache-2.0. Source: https://github.com/mozilla/pdf.js. Included license: `latex-workspace/static/vendor/pdfjs-dist/LICENSE`; standard-font licenses accompany the fonts.
- The optional setup helper can install **Inconsolata**, **algorithms**, and **algorithmicx** from CTAN into the private workspace state directory. These TeX packages are not included in this repository; their distributions contain their applicable licenses.

Browser assets are vendored for offline editing. The restoration helper obtains pinned packages from the npm registry and verifies the archive integrity. AI requests use the selected provider when configured.

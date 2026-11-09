import { getDocument, GlobalWorkerOptions, TextLayer } from '/vendor/pdfjs-dist/build/pdf.mjs';

GlobalWorkerOptions.workerSrc = '/vendor/pdfjs-dist/build/pdf.worker.mjs';

export class PDFPreview {
  constructor(container, controls = {}) {
    this.container = container;
    this.controls = controls;
    this.document = null;
    this.generation = 0;
    this.page = 1;
    this.zoom = 'fit';
    this.rendering = new Set();
    this.tasks = new Set();
    container.addEventListener('scroll', () => this.updatePage(), { passive: true });
    container.addEventListener('dblclick', event => this.locateSource(event));
    container.title = '텍스트를 더블클릭하면 대응하는 LaTeX 소스로 이동합니다';
    controls.previous?.addEventListener('click', () => this.go(this.page - 1));
    controls.next?.addEventListener('click', () => this.go(this.page + 1));
    controls.page?.addEventListener('change', () => this.go(Number(controls.page.value)));
    controls.zoom?.addEventListener('change', () => {
      this.zoom = controls.zoom.value;
      this.layout();
    });
    let timeout;
    this.resizeObserver = new ResizeObserver(() => {
      clearTimeout(timeout);
      timeout = setTimeout(() => { if (this.document && this.zoom === 'fit') this.layout(); }, 180);
    });
    this.resizeObserver.observe(container);
  }

  clear() {
    this.loadSequence = (this.loadSequence || 0) + 1;
    ++this.generation;
    this.observer?.disconnect();
    for (const task of this.tasks) task.cancel();
    this.tasks.clear(); this.rendering.clear();
    this.loading?.destroy().catch(() => {});
    this.loading = null; this.document = null; this.url = null; this.documentURL = null; this.shells = []; this.page = 1; this.lastLocation = null;
    if (this.controls.total) this.controls.total.textContent = '–';
    if (this.controls.page) this.controls.page.value = 1;
    this.updateControls();
  }

  async load(url) {
    if (this.url === url && this.document) return;
    const request = this.loadSequence = (this.loadSequence || 0) + 1;
    ++this.generation;
    this.url = url;
    this.lastLocation = null;
    this.observer?.disconnect();
    for (const task of this.tasks) task.cancel();
    this.tasks.clear();
    this.loading?.destroy().catch(() => {});
    this.document = null;
    this.loading = getDocument({ url, standardFontDataUrl: '/vendor/pdfjs-dist/standard_fonts/', isEvalSupported: false });
    const document = await this.loading.promise;
    if (request !== this.loadSequence) { document.destroy(); return; }
    this.document = document;
    this.documentURL = url;
    if (this.controls.total) this.controls.total.textContent = document.numPages;
    if (this.controls.page) this.controls.page.max = document.numPages;
    this.page = Math.min(this.page, document.numPages);
    await this.layout();
  }

  async layout() {
    if (!this.document) return;
    const document = this.document;
    const generation = ++this.generation;
    const currentPage = this.lastLocation?.page || this.page;
    this.observer?.disconnect();
    for (const task of this.tasks) task.cancel();
    this.tasks.clear();
    this.rendering.clear();
    const first = await document.getPage(1);
    if (generation !== this.generation) return;
    const natural = first.getViewport({ scale: 1 });
    const padding = parseFloat(getComputedStyle(this.container).paddingLeft) * 2;
    this.scale = this.zoom === 'fit' ? Math.max(0.25, (this.container.clientWidth - padding - 4) / natural.width) : Number(this.zoom);
    const width = natural.width * this.scale;
    const height = natural.height * this.scale;
    const pages = window.document.createElement('div');
    pages.className = 'pdf-pages';
    for (let number = 1; number <= document.numPages; number++) {
      const shell = window.document.createElement('div');
      shell.className = 'pdf-page';
      shell.dataset.page = number;
      shell.style.width = `${width}px`;
      shell.style.height = `${height}px`;
      shell.setAttribute('aria-label', `PDF ${number}페이지`);
      pages.append(shell);
    }
    this.container.replaceChildren(pages);
    this.shells = [...pages.children];
    this.observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.isIntersecting) this.render(Number(entry.target.dataset.page), generation);
      }
    }, { root: this.container, rootMargin: '450px 0px' });
    this.shells.forEach(shell => this.observer.observe(shell));
    this.go(currentPage, false);
    await this.render(currentPage, generation);
    if (generation === this.generation && this.lastLocation) await this.reveal(this.lastLocation);
  }

  async render(number, generation) {
    if (!this.document || this.rendering.has(number) || generation !== this.generation) return;
    this.rendering.add(number);
    try {
      const pdfPage = await this.document.getPage(number);
      if (generation !== this.generation) return;
      const shell = this.shells[number - 1];
      const viewport = pdfPage.getViewport({ scale: this.scale });
      shell.pdfGeometry = { viewport, view: pdfPage.view, unit: pdfPage.userUnit };
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const canvas = document.createElement('canvas');
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      shell.style.width = `${viewport.width}px`;
      shell.style.height = `${viewport.height}px`;
      shell.append(canvas);
      const task = pdfPage.render({ canvasContext: canvas.getContext('2d'), viewport, transform: [ratio, 0, 0, ratio, 0, 0] });
      this.tasks.add(task);
      try { await task.promise; } finally { this.tasks.delete(task); }
      if (generation !== this.generation) return;
      shell.classList.add('rendered');
      const layer = document.createElement('div');
      layer.className = 'textLayer';
      layer.style.setProperty('--scale-factor', this.scale);
      shell.append(layer);
      const textLayer = new TextLayer({ textContentSource: pdfPage.streamTextContent(), container: layer, viewport });
      await textLayer.render();
    } catch (error) {
      if (error.name !== 'RenderingCancelledException' && generation === this.generation) console.warn('PDF page:', error);
    }
  }

  async locateSource(event) {
    const span = event.target.closest('.textLayer span');
    const shell = span?.closest('.pdf-page');
    if (!this.document || !shell?.pdfGeometry || !this.controls.onSource) return;
    const { viewport, view, unit } = shell.pdfGeometry;
    const rectangle = shell.getBoundingClientRect();
    // PDF.js reverses rotation and zoom. Canvas device pixels do not enter this calculation.
    const [pdfX, pdfY] = viewport.convertToPdfPoint(
      (event.clientX - rectangle.left) * viewport.width / rectangle.width,
      (event.clientY - rectangle.top) * viewport.height / rectangle.height,
    );
    const parameters = new URL(this.documentURL, location.href).searchParams;
    const selection = window.getSelection();
    const word = selection?.anchorNode && shell.contains(selection.anchorNode) ? selection.toString() : '';
    const position = { project: parameters.get('project'), version: parameters.get('v'),
      page: Number(shell.dataset.page), x: (pdfX - view[0]) * unit, y: (view[3] - pdfY) * unit,
      text: span.textContent, word };
    event.preventDefault();
    const request = this.sourceRequest = (this.sourceRequest || 0) + 1;
    this.container.classList.add('pdf-source-busy');
    try { await this.controls.onSource(position); }
    catch (error) { if (this.controls.onError) this.controls.onError(error); else console.warn('PDF source:', error); }
    finally { if (request === this.sourceRequest) this.container.classList.remove('pdf-source-busy'); }
  }

  go(number, smooth = true) {
    if (!this.document || !this.shells?.length) return;
    number = Math.max(1, Math.min(this.document.numPages, number || 1));
    const shell = this.shells[number - 1];
    this.container.scrollTo({ top: shell.offsetTop - this.container.offsetTop - 24, behavior: smooth ? 'smooth' : 'instant' });
    this.page = number;
    this.updateControls();
  }

  async reveal(location) {
    if (!this.document || !this.shells[location.page - 1]) throw new Error('PDF를 불러온 뒤 다시 시도해 주세요.');
    this.lastLocation = location;
    const generation = this.generation, pdf = this.document;
    const page = await pdf.getPage(location.page);
    if (generation !== this.generation) return;
    await this.render(location.page, generation);
    if (generation !== this.generation) return;
    const viewport = page.getViewport({scale: this.scale}), view = page.view, unit = page.userUnit;
    const box = location.box;
    const [a, b, c, d] = viewport.convertToViewportRectangle([view[0] + box.x / unit, view[3] - box.y / unit,
      view[0] + (box.x + box.width) / unit, view[3] - (box.y + box.height) / unit]);
    const x = Math.min(a, c), y = Math.min(b, d), width = Math.abs(c - a), height = Math.abs(d - b);
    const shell = this.shells[location.page - 1];
    this.container.querySelectorAll('.pdf-location-highlight').forEach(node => node.remove());
    const highlight = document.createElement('div');highlight.className = 'pdf-location-highlight';
    highlight.style.cssText = `left:${Math.max(0,x-3)}px;top:${Math.max(0,y-3)}px;width:${Math.max(5,width+6)}px;height:${Math.max(7,height+6)}px`;
    highlight.setAttribute('aria-label','선택한 소스의 PDF 위치');shell.append(highlight);
    clearTimeout(this.highlightTimer);this.highlightTimer = setTimeout(() => {highlight.remove();this.lastLocation = null;},5000);
    const containerRect = this.container.getBoundingClientRect(), shellRect = shell.getBoundingClientRect();
    this.container.scrollTo({top:this.container.scrollTop + shellRect.top - containerRect.top + y - this.container.clientHeight * .35,
      left:this.container.scrollLeft + shellRect.left - containerRect.left + x + width / 2 - this.container.clientWidth / 2, behavior:'instant'});
    this.page = location.page;this.updateControls();
  }

  updatePage() {
    if (!this.shells?.length) return;
    const top = this.container.getBoundingClientRect().top;
    let closest = 1;
    for (let i = 0; i < this.shells.length; i++) {
      if (this.shells[i].getBoundingClientRect().top < top + this.container.clientHeight * 0.4) closest = i + 1;
      else break;
    }
    this.page = closest;
    this.updateControls();
  }

  updateControls() {
    if (this.controls.page) this.controls.page.value = this.page;
    if (this.controls.previous) this.controls.previous.disabled = this.page <= 1;
    if (this.controls.next) this.controls.next.disabled = !this.document || this.page >= this.document.numPages;
  }
}

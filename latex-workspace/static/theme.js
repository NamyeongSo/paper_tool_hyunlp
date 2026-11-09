/* Apply the saved preference before styles paint, including the separate PDF window. */
(() => {
  'use strict';
  const key = 'folio:theme', root = document.documentElement;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = null;
  try { const saved = localStorage.getItem(key); if (saved === 'light' || saved === 'dark') preference = saved; } catch {}
  function apply() {
    const dark = preference ? preference === 'dark' : system.matches;
    root.dataset.theme = dark ? 'dark' : 'light';
    root.style.colorScheme = root.dataset.theme;
    for (const button of document.querySelectorAll('[data-theme-toggle]')) {
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', dark ? '라이트 모드로 전환' : '다크 모드로 전환');
      button.title = dark ? '라이트 모드로 전환' : '다크 모드로 전환';
      const label = button.querySelector('.theme-toggle-label');
      if (label) label.textContent = dark ? '라이트 모드' : '다크 모드';
    }
  }
  apply();
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-theme-toggle]')) return;
    preference = root.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(key, preference); } catch {}
    apply();
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply, {once:true});
  else apply();
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = event.newValue === 'light' || event.newValue === 'dark' ? event.newValue : null;
    apply();
  });
  const changed = () => { if (!preference) apply(); };
  if (system.addEventListener) system.addEventListener('change', changed);
  else system.addListener(changed);
})();

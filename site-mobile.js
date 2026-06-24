(function () {
  var breakpoint = 720;

  function anyMenuOpen() {
    return !!document.querySelector('.site-header.is-menu-open');
  }

  function setBodyLock() {
    document.body.classList.toggle('site-menu-lock', anyMenuOpen());
  }

  function setOpen(header, open) {
    var button = header.querySelector('.site-menu-button');
    header.classList.toggle('is-menu-open', open);
    if (button) {
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
      button.setAttribute('aria-label', open ? 'メニューを閉じる' : 'メニューを開く');
    }
    setBodyLock();
  }

  function closeAllMenus() {
    document.querySelectorAll('.site-header.is-menu-open').forEach(function (header) {
      setOpen(header, false);
    });
  }

  function initHeader(header) {
    if (!header || header.dataset.menuReady === 'true') return;

    var button = header.querySelector('.site-menu-button');
    var closeButton = header.querySelector('.site-menu-close');
    var backdrop = header.querySelector('.site-menu-backdrop');
    var nav = header.querySelector('.site-nav');
    if (!button || !nav) return;

    header.dataset.menuReady = 'true';

    button.addEventListener('click', function () {
      var open = !header.classList.contains('is-menu-open');
      setOpen(header, open);
    });

    if (backdrop) {
      backdrop.addEventListener('click', function () {
        setOpen(header, false);
      });
    }

    if (closeButton) {
      closeButton.addEventListener('click', function () {
        setOpen(header, false);
      });
    }

    nav.addEventListener('click', function (event) {
      if (event.target.closest('a')) setOpen(header, false);
    });
  }

  function initMenus() {
    document.querySelectorAll('.site-header').forEach(initHeader);
  }

  function onResize() {
    if (window.innerWidth > breakpoint) closeAllMenus();
  }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeAllMenus();
  });

  window.addEventListener('resize', onResize);
  document.addEventListener('DOMContentLoaded', initMenus);
  window.addEventListener('load', initMenus);

  if (document.body) {
    var observer = new MutationObserver(initMenus);
    observer.observe(document.body, { childList: true, subtree: true });
    initMenus();
  }
})();

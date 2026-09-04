/* Ghost admin pages (admin_clients.html, index.html, student.html) reload
 * the WHOLE page after almost every save/action (~60 call sites combined)
 * instead of patching just the changed DOM — a real refactor of that would
 * be a large, risky rewrite. What's actually broken about it, reported by
 * the coach as "a button reload takes me back to a previous tab": a plain
 * location.reload() always resets scroll to the top. On a long list (37+
 * Ghosts, a full payments/devoirs page) that reads exactly like "it kicked
 * me back" even though the hash-based section itself restores correctly.
 * This preserves scroll position across that reload — same interaction,
 * same code paths, the coach just doesn't lose their place. */
(function () {
  function reloadPreservingScroll() {
    try {
      sessionStorage.setItem('ghost_scroll_' + location.pathname, String(window.scrollY));
    } catch (e) {}
    location.reload();
  }

  function restoreScrollIfSaved() {
    try {
      var key = 'ghost_scroll_' + location.pathname;
      var saved = sessionStorage.getItem(key);
      if (saved === null) return;
      sessionStorage.removeItem(key);
      var y = parseInt(saved, 10);
      if (isNaN(y) || y <= 0) return;
      // The page's own hash-based section restore (which changes layout
      // height) runs on the same DOMContentLoaded tick — two delayed
      // attempts land after that settles instead of racing it.
      setTimeout(function () { window.scrollTo(0, y); }, 60);
      setTimeout(function () { window.scrollTo(0, y); }, 300);
    } catch (e) {}
  }

  window.reloadPreservingScroll = reloadPreservingScroll;
  document.addEventListener('DOMContentLoaded', restoreScrollIfSaved);
})();

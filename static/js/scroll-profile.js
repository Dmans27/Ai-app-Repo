(() => {
  const THRESHOLD_PX = 100;

  function init() {
    const el = document.getElementById("scroll-profile");
    if (!el) return;

    // Always use the browser's actual scroller
    const scroller = document.scrollingElement || document.documentElement;

    function toggle() {
      const y = window.scrollY || scroller.scrollTop || 0;
      el.classList.toggle("is-visible", y >= THRESHOLD_PX);
    }

    window.addEventListener("scroll", toggle, { passive: true });
    window.addEventListener("resize", toggle);

    toggle(); // run once on load
  }

  document.addEventListener("DOMContentLoaded", init);
})();



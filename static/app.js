document.addEventListener('DOMContentLoaded', () => {
  const forms = document.querySelectorAll('form');
  forms.forEach((form) => {
    form.addEventListener('submit', () => {
      const submit = form.querySelector('button[type="submit"]');
      if (submit && !submit.dataset.locked) {
        submit.dataset.locked = '1';
        submit.disabled = true;
        submit.style.opacity = '.7';
      }
    });
  });
});

// Background gallery: keep motion subtle, controllable, and safe for keyboard users.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.blog-gallery').forEach((gallery) => {
    const slides = Array.from(gallery.querySelectorAll('.blog-gallery__slide'));
    const pauseButton = gallery.querySelector('.blog-gallery__pause');
    if (slides.length < 2) {
      if (pauseButton) pauseButton.hidden = true;
      return;
    }

    const effect = ['fade', 'slide', 'zoom'].includes(gallery.dataset.effect)
      ? gallery.dataset.effect
      : 'fade';
    const interval = Math.max(1, Number.parseInt(gallery.dataset.interval, 10) || 5) * 1000;
    const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let current = slides.findIndex((slide) => slide.classList.contains('is-active'));
    let timer = null;
    let manuallyPaused = false;
    let hoverPaused = false;
    let focusPaused = false;
    if (current < 0) current = 0;

    gallery.dataset.effect = effect;

    const setPausedLabel = () => {
      if (!pauseButton) return;
      pauseButton.textContent = manuallyPaused ? '恢复轮播' : '暂停轮播';
      pauseButton.setAttribute('aria-label', manuallyPaused ? '恢复背景轮播' : '暂停背景轮播');
      pauseButton.setAttribute('aria-pressed', String(manuallyPaused));
    };

    const stop = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };

    const start = () => {
      if (reducedMotion || manuallyPaused || hoverPaused || focusPaused || timer !== null) return;
      timer = window.setInterval(() => {
        const next = (current + 1) % slides.length;
        slides[current].classList.remove('is-active');
        slides[next].classList.add('is-active');
        current = next;
      }, interval);
    };

    const sync = () => {
      if (reducedMotion || manuallyPaused || hoverPaused || focusPaused) stop();
      else start();
    };

    if (pauseButton) {
      pauseButton.addEventListener('click', () => {
        manuallyPaused = !manuallyPaused;
        setPausedLabel();
        sync();
      });
    }
    gallery.addEventListener('mouseenter', () => { hoverPaused = true; sync(); });
    gallery.addEventListener('mouseleave', () => { hoverPaused = false; sync(); });
    gallery.addEventListener('focusin', () => { focusPaused = true; sync(); });
    gallery.addEventListener('focusout', (event) => {
      if (!gallery.contains(event.relatedTarget)) {
        focusPaused = false;
        sync();
      }
    });

    setPausedLabel();
    sync();
  });
});

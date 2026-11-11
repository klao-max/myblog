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

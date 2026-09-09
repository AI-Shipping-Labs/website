(function() {
  function confirmAction(event, element) {
    var message = element.getAttribute('data-confirm');
    if (!message || window.confirm(message)) {
      return;
    }
    event.preventDefault();
  }

  document.addEventListener('submit', function(event) {
    var form = event.target;
    if (!form || !form.matches || !form.matches('form[data-confirm]')) {
      return;
    }
    confirmAction(event, form);
  }, true);

  document.addEventListener('click', function(event) {
    var control = event.target.closest && event.target.closest(
      'button[type="submit"][formaction][data-confirm]'
    );
    if (!control) {
      return;
    }
    confirmAction(event, control);
  });
})();

(function () {
  function getCsrfToken() {
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
      var cookie = cookies[i].trim();
      if (cookie.startsWith('csrftoken=')) {
        return cookie.substring('csrftoken='.length);
      }
    }
    return '';
  }

  function parseJsonResponse(resp) {
    return resp.json().then(function (data) {
      return { status: resp.status, data: data };
    });
  }

  function setPendingState(config, isPending) {
    var form = document.getElementById(config.formId);
    var submitButton = document.getElementById(config.submitId);
    var submitText = document.getElementById(config.submitTextId);
    if (form) {
      form.setAttribute('aria-busy', isPending ? 'true' : 'false');
    }
    if (submitButton) {
      submitButton.disabled = isPending;
      submitButton.setAttribute('aria-busy', isPending ? 'true' : 'false');
    }
    if (submitText && submitButton) {
      submitText.textContent = isPending
        ? submitButton.dataset.loadingText
        : submitButton.dataset.idleText;
    }
  }

  function showMessage(elementId, message) {
    var element = document.getElementById(elementId);
    element.textContent = message;
    element.classList.remove('hidden');
  }

  function hideMessage(elementId) {
    var element = document.getElementById(elementId);
    element.textContent = '';
    element.classList.add('hidden');
  }

  window.authHelpers = {
    getCsrfToken: getCsrfToken,
    parseJsonResponse: parseJsonResponse,
    setPendingState: setPendingState,
    showMessage: showMessage,
    hideMessage: hideMessage,
  };
})();

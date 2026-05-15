// Inline register form handler (issue #652).
//
// Extracted from templates/accounts/register.html so the standalone
// register page and the inline registration surfaces (course detail,
// workshop pages paywall, pricing free tier) share a single source of
// truth. The surface template must:
//
//   1. Include /static/js/accounts/auth-helpers.js before this file.
//   2. Emit {{ next_url|json_script:"auth-next-url" }} so the script
//      can read the round-trip URL out of the DOM.
//
// On success (HTTP 201) the success message renders inline with a
// "Return to where you left off." link that points at return_url —
// no auto-redirect happens so the visitor stays on the originating
// page and can pick up their verification email at leisure.

(function () {
  var registerPending = false;
  var nextUrlElement = document.getElementById('auth-next-url');
  var authNextUrl = '';
  if (nextUrlElement) {
    try {
      authNextUrl = JSON.parse(nextUrlElement.textContent || '""');
    } catch (e) {
      authNextUrl = '';
    }
  }

  function setRegisterPending(isPending) {
    window.authHelpers.setPendingState({
      formId: 'register-form',
      submitId: 'register-submit',
      submitTextId: 'register-submit-text',
    }, isPending);
  }

  function handleRegister(event) {
    event.preventDefault();
    if (registerPending) {
      return false;
    }

    var email = document.getElementById('register-email').value;
    var password = document.getElementById('register-password').value;
    var passwordConfirm = document.getElementById('register-password-confirm').value;

    window.authHelpers.hideMessage('register-error');
    window.authHelpers.hideMessage('register-success');

    if (password !== passwordConfirm) {
      window.authHelpers.showMessage('register-error', 'Passwords do not match');
      return false;
    }

    registerPending = true;
    setRegisterPending(true);

    fetch('/api/register', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': window.authHelpers.getCsrfToken(),
      },
      body: JSON.stringify({ email: email, password: password, next: authNextUrl }),
    })
      .then(window.authHelpers.parseJsonResponse)
      .then(function (result) {
        registerPending = false;
        setRegisterPending(false);
        if (result.status === 201) {
          window.authHelpers.showMessage(
            'register-success',
            result.data.message || 'Account created successfully!'
          );
          if (result.data.return_url) {
            var successBox = document.getElementById('register-success');
            var returnLink = document.createElement('a');
            returnLink.href = result.data.return_url;
            returnLink.className = 'ml-1 text-accent hover:underline';
            returnLink.textContent = 'Return to where you left off.';
            successBox.appendChild(returnLink);
          }
          document.getElementById('register-form').reset();
        } else {
          window.authHelpers.showMessage(
            'register-error',
            result.data.error || 'Registration failed'
          );
        }
      })
      .catch(function () {
        registerPending = false;
        setRegisterPending(false);
        window.authHelpers.showMessage(
          'register-error',
          'An error occurred. Please try again.'
        );
      });

    return false;
  }

  // Exposed globally so the form's inline onsubmit="return handleRegister(event)"
  // (defined in _register_form.html) finds it.
  window.handleRegister = handleRegister;
})();

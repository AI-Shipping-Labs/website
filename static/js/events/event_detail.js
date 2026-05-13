(function () {
  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i += 1) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === `${name}=`) {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  function setButtonBusy(button, label) {
    if (!button) return;
    button.dataset.originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = label;
  }

  function restoreButton(button) {
    if (!button) return;
    button.disabled = false;
    button.textContent = button.dataset.originalLabel || button.textContent;
  }

  function registerForEvent(slug, button) {
    setButtonBusy(button, 'Registering...');

    fetch(`/api/events/${slug}/register`, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'Content-Type': 'application/json',
      },
    })
      .then((response) => {
        if (response.ok) {
          window.location.reload();
          return null;
        }
        return response.json().then((data) => {
          alert(data.error || 'Registration failed');
          restoreButton(button);
        });
      })
      .catch(() => {
        alert('Network error. Please try again.');
        restoreButton(button);
      });
  }

  function unregisterFromEvent(slug, button) {
    if (!confirm('Are you sure you want to cancel your registration?')) return;

    setButtonBusy(button, 'Cancelling...');

    fetch(`/api/events/${slug}/unregister`, {
      method: 'DELETE',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
      },
    })
      .then((response) => {
        if (response.ok) {
          window.location.reload();
          return null;
        }
        return response.json().then((data) => {
          alert(data.error || 'Unregistration failed');
          restoreButton(button);
        });
      })
      .catch(() => {
        alert('Network error. Please try again.');
        restoreButton(button);
      });
  }

  function bindAnonymousRegistration(slug) {
    const form = document.getElementById('event-anon-register-form');
    if (!form || !slug) return;

    const btn = document.getElementById('event-anon-submit-btn');
    const errEl = document.getElementById('event-anon-error');
    const emailInput = document.getElementById('event-anon-email');

    function showError(message) {
      if (!errEl) {
        alert(message);
        return;
      }
      errEl.textContent = message;
      errEl.classList.remove('hidden');
    }

    function clearError() {
      if (!errEl) return;
      errEl.textContent = '';
      errEl.classList.add('hidden');
    }

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      clearError();
      const email = (emailInput.value || '').trim();
      if (!email) {
        showError('Please enter your email address.');
        return;
      }

      setButtonBusy(btn, 'Registering...');

      fetch(`/api/events/${slug}/register`, {
        method: 'POST',
        headers: {
          'X-CSRFToken': getCookie('csrftoken'),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email }),
      })
        .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
        .then((result) => {
          if (result.ok) {
            const url = new URL(window.location.href);
            url.searchParams.set('registered', email);
            if (result.data && result.data.account_created) {
              url.searchParams.set('account_created', '1');
            }
            window.location.href = url.toString();
          } else {
            showError((result.data && result.data.error) || 'Registration failed.');
            restoreButton(btn);
          }
        })
        .catch(() => {
          showError('Network error. Please try again.');
          restoreButton(btn);
        });
    });
  }

  function isValidTimeZone(timezoneName) {
    if (!timezoneName || typeof timezoneName !== 'string') return false;
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: timezoneName }).format(new Date());
      return true;
    } catch (error) {
      return false;
    }
  }

  function getBrowserTimeZone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch (error) {
      return null;
    }
  }

  function resolveTimeZone(display) {
    const defaultTimeZone = display.dataset.defaultTimezone || 'Europe/Berlin';
    if (display.dataset.browserTimezoneEnabled !== 'true') return defaultTimeZone;
    const browserTimeZone = getBrowserTimeZone();
    if (isValidTimeZone(browserTimeZone)) return browserTimeZone;
    if (isValidTimeZone(defaultTimeZone)) return defaultTimeZone;
    return 'Europe/Berlin';
  }

  function formatDate(value, timezoneName) {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: timezoneName,
      month: 'long',
      day: 'numeric',
      year: 'numeric',
    }).format(value);
  }

  function formatTime(value, timezoneName) {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: timezoneName,
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).format(value);
  }

  function renderEventTime(display, timezoneName) {
    const start = new Date(display.dataset.startUtc);
    const endValue = display.dataset.endUtc;
    const startDate = formatDate(start, timezoneName);
    const startTime = formatTime(start, timezoneName);
    if (!endValue) {
      display.textContent = `${startDate}, ${startTime} ${timezoneName}`;
      return;
    }

    const end = new Date(endValue);
    const endDate = formatDate(end, timezoneName);
    const endTime = formatTime(end, timezoneName);
    if (startDate === endDate) {
      display.textContent = `${startDate}, ${startTime}-${endTime} ${timezoneName}`;
    } else {
      display.textContent = `${startDate}, ${startTime} - ${endDate}, ${endTime} ${timezoneName}`;
    }
  }

  const root = document.querySelector('[data-event-detail]');
  const slug = root ? root.dataset.eventSlug : null;
  const registerBtn = document.querySelector('[data-event-register-button]');
  const unregisterBtn = document.querySelector('[data-event-unregister-button]');
  const display = document.querySelector('[data-event-time-display]');

  if (registerBtn && slug) {
    registerBtn.addEventListener('click', () => registerForEvent(slug, registerBtn));
  }
  if (unregisterBtn && slug) {
    unregisterBtn.addEventListener('click', () => unregisterFromEvent(slug, unregisterBtn));
  }
  bindAnonymousRegistration(slug);

  if (display && window.Intl) {
    renderEventTime(display, resolveTimeZone(display));
  }
}());

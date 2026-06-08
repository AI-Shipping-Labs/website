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

  function setBusy(button, label) {
    if (!button) return;
    button.dataset.originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = label;
  }

  function restore(button) {
    if (!button) return;
    button.disabled = false;
    button.textContent = button.dataset.originalLabel || button.textContent;
  }

  function buildSummaryText(summary) {
    if (!summary) return '';
    const registered = summary.registered || 0;
    const total = summary.total_occurrences || 0;
    const noAccess = summary.skipped_no_access || 0;
    let text;
    if (noAccess > 0) {
      text = `Registered for ${registered} of ${total} occurrences — ${noAccess} require a higher tier.`;
    } else {
      const noun = registered === 1 ? 'occurrence' : 'occurrences';
      text = `Registered for ${registered} ${noun}.`;
    }
    return text;
  }

  function showSummary(panel, summary) {
    const node = panel.querySelector('[data-series-summary]');
    if (!node) return;
    const text = buildSummaryText(summary);
    if (!text) return;
    node.textContent = text;
    node.classList.remove('hidden');
  }

  function reloadAfter(callback) {
    let reloaded = false;
    const reload = () => {
      if (reloaded) return;
      reloaded = true;
      window.location.reload();
    };
    if (callback) callback(reload);
    // Always reload so the page reflects the new per-occurrence states.
    setTimeout(reload, 1200);
  }

  function registerSeries(panel, button) {
    const url = panel.dataset.seriesRegisterUrl;
    setBusy(button, 'Registering...');

    fetch(url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('csrftoken'),
        'Content-Type': 'application/json',
      },
    })
      .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
      .then(({ ok, data }) => {
        if (!ok) {
          window.alert((data && data.error) || 'Registration failed');
          restore(button);
          return;
        }
        showSummary(panel, data.summary);
        reloadAfter((reload) => {
          if (typeof window.gtag === 'function') {
            window.gtag('event', 'series_register', {
              series_slug: panel.dataset.seriesSlug,
              event_callback: reload,
            });
          }
        });
      })
      .catch(() => {
        window.alert('Network error. Please try again.');
        restore(button);
      });
  }

  function cancelSeries(panel, button) {
    if (!window.confirm('Cancel your registration for this whole series? Future sessions will be removed.')) {
      return;
    }
    const url = panel.dataset.seriesRegisterUrl;
    setBusy(button, 'Cancelling...');

    fetch(url, {
      method: 'DELETE',
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
          window.alert((data && data.error) || 'Could not cancel series registration');
          restore(button);
        });
      })
      .catch(() => {
        window.alert('Network error. Please try again.');
        restore(button);
      });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const panel = document.querySelector('[data-testid="series-register-panel"]');
    if (!panel) return;

    const registerBtn = panel.querySelector('[data-series-register]');
    if (registerBtn) {
      registerBtn.addEventListener('click', () => registerSeries(panel, registerBtn));
    }

    const cancelBtn = panel.querySelector('[data-series-cancel]');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => cancelSeries(panel, cancelBtn));
    }
  });
})();

(function() {
  var LEAVE_MESSAGE = 'You have unsaved changes. Leave without saving?';
  var guardedForms = [];

  function isIgnoredControl(control) {
    if (!control || control.disabled || control.readOnly) {
      return true;
    }
    if (control.matches('[data-dirty-ignore], [data-studio-dirty-ignore]')) {
      return true;
    }
    var type = (control.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden' || type === 'submit' || type === 'button' ||
        type === 'reset' || type === 'image') {
      return true;
    }
    if (control.name === 'csrfmiddlewaretoken') {
      return true;
    }
    return false;
  }

  function trackedControls(form) {
    return Array.prototype.slice.call(
      form.querySelectorAll('input, textarea, select')
    ).filter(function(control) {
      return !isIgnoredControl(control);
    });
  }

  function controlValue(control) {
    var tagName = control.tagName.toLowerCase();
    var type = (control.getAttribute('type') || '').toLowerCase();

    if (type === 'checkbox' || type === 'radio') {
      return control.checked ? '1' : '0';
    }
    if (type === 'file') {
      return control.files && control.files.length ? String(control.files.length) : '';
    }
    if (tagName === 'select' && control.multiple) {
      return Array.prototype.slice.call(control.options)
        .filter(function(option) { return option.selected; })
        .map(function(option) { return option.value; })
        .join('\n');
    }
    return control.value || '';
  }

  function snapshot(form) {
    return trackedControls(form).map(function(control, index) {
      var key = control.name || control.id || String(index);
      return key + '=' + controlValue(control);
    }).join('\u001f');
  }

  function setStatus(guard, state) {
    if (!guard.status) {
      return;
    }
    var text = 'No unsaved changes';
    var classes = 'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ';
    if (state === 'dirty') {
      text = 'Unsaved changes';
      classes += 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300';
    } else if (state === 'saving') {
      text = 'Saving\u2026';
      classes += 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300';
    } else if (state === 'error') {
      text = 'Save failed - fix errors';
      classes += 'border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400';
    } else {
      classes += 'border-border bg-secondary text-muted-foreground';
    }
    guard.status.textContent = text;
    guard.status.className = classes;
    guard.status.setAttribute('data-studio-dirty-status-state', state);
  }

  function updateGuard(guard) {
    if (guard.submitting) {
      return;
    }
    var dirty = snapshot(guard.form) !== guard.initialSnapshot;
    guard.dirty = dirty;
    if (!dirty && guard.preserveInitialError) {
      setStatus(guard, 'error');
      return;
    }
    if (dirty) {
      guard.preserveInitialError = false;
    }
    setStatus(guard, dirty ? 'dirty' : 'clean');
  }

  function isSamePageAnchor(anchor) {
    var href = anchor.getAttribute('href') || '';
    if (!href || href.charAt(0) === '#') {
      return true;
    }
    return anchor.pathname === window.location.pathname &&
      anchor.search === window.location.search &&
      !!anchor.hash;
  }

  function shouldInterceptLink(anchor) {
    if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) {
      return false;
    }
    if (anchor.origin !== window.location.origin) {
      return false;
    }
    return !isSamePageAnchor(anchor);
  }

  function hasDirtyForm() {
    return guardedForms.some(function(guard) {
      updateGuard(guard);
      return guard.dirty && !guard.submitting;
    });
  }

  function initGuard(bar) {
    var formId = bar.getAttribute('data-studio-dirty-guard-form');
    var form = formId ? document.getElementById(formId) : null;
    if (!form) {
      return;
    }
    var guard = {
      form: form,
      status: bar.querySelector('[data-studio-dirty-status]'),
      initialSnapshot: snapshot(form),
      dirty: false,
      submitting: false,
    };
    guard.preserveInitialError = guard.status &&
      guard.status.getAttribute('data-studio-dirty-status-state') === 'error';
    guardedForms.push(guard);

    form.addEventListener('input', function() {
      updateGuard(guard);
    });
    form.addEventListener('change', function() {
      updateGuard(guard);
    });
    form.addEventListener('submit', function(event) {
      if (event.defaultPrevented) {
        return;
      }
      guard.submitting = true;
      guard.dirty = false;
      setStatus(guard, 'saving');
    });
    updateGuard(guard);
  }

  document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('[data-studio-dirty-guard-form]').forEach(initGuard);
  });

  window.addEventListener('beforeunload', function(event) {
    if (!hasDirtyForm()) {
      return;
    }
    event.preventDefault();
    event.returnValue = LEAVE_MESSAGE;
    return LEAVE_MESSAGE;
  });

  document.addEventListener('click', function(event) {
    var anchor = event.target.closest && event.target.closest('a[href]');
    if (!shouldInterceptLink(anchor)) {
      return;
    }
    if (!hasDirtyForm()) {
      return;
    }
    if (window.confirm(LEAVE_MESSAGE)) {
      guardedForms.forEach(function(guard) {
        guard.submitting = true;
        guard.dirty = false;
      });
      return;
    }
    event.preventDefault();
    event.stopPropagation();
  }, true);

  window.studioDirtyFormGuard = {
    hasDirtyForm: hasDirtyForm,
    snapshot: snapshot,
    trackedControls: trackedControls,
  };
})();

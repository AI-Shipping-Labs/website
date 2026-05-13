(function () {
  'use strict';

  const root = document.getElementById('member-plan');
  if (!root) { return; }

  const apiBase = root.dataset.apiBase || '/api/';
  const apiToken = root.dataset.apiToken || '';
  const endpoints = {
    checkpoint: 'checkpoints/',
    deliverable: 'deliverables/',
    'next-step': 'next-steps/',
  };

  function endpointFor(item) {
    return endpoints[item.dataset.itemType] + item.dataset.itemId;
  }

  function getCookie(name) {
    const prefix = name + '=';
    return document.cookie.split(';').map(function (cookie) {
      return cookie.trim();
    }).find(function (cookie) {
      return cookie.startsWith(prefix);
    })?.substring(prefix.length) || '';
  }

  function setStatus(item, text, state) {
    const status = item.querySelector('[data-save-status]');
    if (!status) { return; }
    status.textContent = text || '';
    status.className = state === 'failed'
      ? 'text-destructive'
      : 'text-muted-foreground';
  }

  function apiPatch(path, body) {
    const headers = {
      'Content-Type': 'application/json',
    };
    if (apiToken) {
      headers.Authorization = 'Token ' + apiToken;
    } else {
      headers['X-CSRFToken'] = getCookie('csrftoken');
    }
    return fetch(apiBase + path.replace(/^\//, ''), {
      method: 'PATCH',
      headers: headers,
      body: JSON.stringify(body),
    }).then(function (response) {
      if (!response.ok) {
        const error = new Error('HTTP ' + response.status);
        error.status = response.status;
        throw error;
      }
      return response.json();
    });
  }

  function apiPatchWithRetry(path, body) {
    return apiPatch(path, body).catch(function () {
      return new Promise(function (resolve, reject) {
        setTimeout(function () {
          apiPatch(path, body).then(resolve).catch(reject);
        }, 1000);
      });
    });
  }

  function postJson(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    }).then(function (response) {
      return response.json().catch(function () {
        return {};
      }).then(function (data) {
        if (!response.ok || data.ok === false) {
          const error = new Error(data.error || 'Save failed. Try again.');
          error.status = response.status;
          throw error;
        }
        return data;
      });
    });
  }

  function updateCompleteState(item, done) {
    const rendered = item.querySelector('[data-rendered-markdown]');
    item.classList.toggle('is-complete', done);
    if (!rendered) { return; }
    rendered.classList.toggle('line-through', done);
    rendered.classList.toggle('text-muted-foreground', done);
    rendered.classList.toggle('text-foreground', !done);
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
  }

  function renderInline(text) {
    let html = escapeHtml(text);
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|mailto:[^)\s]+|\/[^)\s]*|#[^)\s]*)\)/g, function (_match, label, url) {
      return '<a href="' + url + '" rel="noopener noreferrer">' + label + '</a>';
    });
    return html;
  }

  function renderMarkdown(markdown) {
    const lines = (markdown || '').split(/\r?\n/);
    const blocks = [];
    let paragraph = [];
    let list = [];
    let inCode = false;
    let code = [];

    function flushParagraph() {
      if (paragraph.length) {
        blocks.push('<p>' + renderInline(paragraph.join(' ')) + '</p>');
        paragraph = [];
      }
    }
    function flushList() {
      if (list.length) {
        blocks.push('<ul>' + list.map(function (item) {
          return '<li>' + renderInline(item) + '</li>';
        }).join('') + '</ul>');
        list = [];
      }
    }

    lines.forEach(function (line) {
      if (line.trim().startsWith('```')) {
        if (inCode) {
          blocks.push('<pre><code>' + escapeHtml(code.join('\n')) + '</code></pre>');
          code = [];
          inCode = false;
        } else {
          flushParagraph();
          flushList();
          inCode = true;
        }
        return;
      }
      if (inCode) {
        code.push(line);
        return;
      }
      const listMatch = line.match(/^\s*[-*]\s+(.+)$/);
      if (listMatch) {
        flushParagraph();
        list.push(listMatch[1]);
        return;
      }
      if (!line.trim()) {
        flushParagraph();
        flushList();
        return;
      }
      flushList();
      paragraph.push(line.trim());
    });

    if (inCode) {
      blocks.push('<pre><code>' + escapeHtml(code.join('\n')) + '</code></pre>');
    }
    flushParagraph();
    flushList();
    return blocks.join('');
  }

  // Issue #583: visibility toggle. Replaces the legacy <select> + Save
  // form. Clicking the switch POSTs to update_plan_visibility with
  // ``Accept: application/json`` so the server returns JSON instead of
  // redirecting; on success we flip the toggle, update the helper text
  // and the visible label, and show a short "Saved" indicator. On
  // failure we revert the toggle and show an inline error.
  const visibilityControl = document.querySelector(
    '[data-plan-visibility-control]'
  );
  if (visibilityControl) {
    const toggle = visibilityControl.querySelector(
      '[data-testid="plan-visibility-toggle"]'
    );
    const status = visibilityControl.querySelector('[data-toggle-status]');
    const thumb = visibilityControl.querySelector('[data-toggle-thumb]');
    const label = document.querySelector(
      '[data-testid="plan-visibility-label"]'
    );
    const helper = document.querySelector(
      '[data-testid="plan-visibility-helper"]'
    );
    const updateUrl = visibilityControl.dataset.updateUrl;
    let statusTimer = null;

    function setToggleState(isCohort) {
      toggle.setAttribute('aria-checked', isCohort ? 'true' : 'false');
      toggle.classList.toggle('bg-accent', isCohort);
      toggle.classList.toggle('bg-secondary', !isCohort);
      thumb.classList.toggle('translate-x-5', isCohort);
      thumb.classList.toggle('translate-x-0.5', !isCohort);
      if (label) {
        label.textContent = isCohort ? 'Shared with cohort' : 'Private';
      }
      if (helper) {
        helper.textContent = isCohort
          ? 'Visible to other members of the same sprint on the cohort board.'
          : 'Only you and the team can see this plan.';
      }
    }

    function setStatusText(text, state) {
      if (!status) { return; }
      if (statusTimer) {
        clearTimeout(statusTimer);
        statusTimer = null;
      }
      status.textContent = text || '';
      status.classList.remove('text-destructive', 'text-muted-foreground');
      status.classList.add(
        state === 'error' ? 'text-destructive' : 'text-muted-foreground'
      );
      status.style.opacity = '1';
    }

    function fadeStatusAfter(ms) {
      if (statusTimer) { clearTimeout(statusTimer); }
      statusTimer = setTimeout(function () {
        if (status) {
          status.textContent = '';
          status.style.opacity = '';
        }
      }, ms);
    }

    toggle.addEventListener('click', function () {
      const wasCohort = toggle.getAttribute('aria-checked') === 'true';
      const nextValue = wasCohort ? 'private' : 'cohort';
      const willBeCohort = !wasCohort;

      // Optimistic flip so the UI feels instant; we revert on error.
      setToggleState(willBeCohort);
      setStatusText('Saving...', 'saving');
      toggle.disabled = true;

      const formData = new FormData();
      formData.append('visibility', nextValue);

      fetch(updateUrl, {
        method: 'POST',
        headers: {
          'Accept': 'application/json',
          'X-CSRFToken': getCookie('csrftoken'),
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: formData,
        credentials: 'same-origin',
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error('HTTP ' + response.status);
          }
          setStatusText('Saved', 'saved');
          fadeStatusAfter(1500);
        })
        .catch(function () {
          setToggleState(wasCohort);
          setStatusText("Couldn't save — try again.", 'error');
        })
        .finally(function () {
          toggle.disabled = false;
        });
    });
  }

  const goalEditor = root.querySelector('[data-plan-goal-editor]');
  if (goalEditor) {
    const rendered = goalEditor.querySelector('[data-plan-goal-text]');
    const input = goalEditor.querySelector('[data-plan-goal-input]');
    const edit = goalEditor.querySelector('[data-plan-goal-edit]');
    const save = goalEditor.querySelector('[data-plan-goal-save]');
    const cancel = goalEditor.querySelector('[data-plan-goal-cancel]');
    const status = goalEditor.querySelector('[data-plan-goal-status]');
    const updateUrl = goalEditor.dataset.updateUrl;
    const placeholder = "Add a one-sentence goal so teammates know what you're shipping this sprint.";
    let original = input ? input.value : '';

    function setGoalStatus(text, state) {
      if (!status) { return; }
      status.textContent = text || '';
      status.className = state === 'failed'
        ? 'text-destructive'
        : 'text-muted-foreground';
    }

    function setGoalEditing(isEditing) {
      if (!rendered || !input || !edit || !save || !cancel) { return; }
      input.classList.toggle('hidden', !isEditing);
      save.classList.toggle('hidden', !isEditing);
      cancel.classList.toggle('hidden', !isEditing);
      edit.classList.toggle('hidden', isEditing);
      rendered.classList.toggle('hidden', isEditing);
      if (isEditing) {
        input.focus();
        input.select();
      }
    }

    if (edit && save && cancel && input && rendered && updateUrl) {
      edit.addEventListener('click', function () {
        original = input.value;
        setGoalStatus('', 'saved');
        setGoalEditing(true);
      });
      cancel.addEventListener('click', function () {
        input.value = original;
        setGoalStatus('', 'saved');
        setGoalEditing(false);
      });
      save.addEventListener('click', function () {
        const value = input.value;
        if (value.length > 280) {
          setGoalStatus('Goal must be 280 characters or fewer.', 'failed');
          return;
        }
        setGoalStatus('Saving...', 'saving');
        postJson(updateUrl, {goal: value})
          .then(function (data) {
            original = data.goal || '';
            input.value = original;
            rendered.innerHTML = original ? renderMarkdown(original) : escapeHtml(placeholder);
            rendered.classList.toggle('text-muted-foreground', !original);
            rendered.classList.toggle('italic', !original);
            setGoalEditing(false);
            setGoalStatus('Saved', 'saved');
          })
          .catch(function (error) {
            setGoalStatus(error.message || 'Save failed. Try again.', 'failed');
          });
      });
    }
  }

  root.querySelectorAll('[data-plan-item]').forEach(function (item) {
    const checkbox = item.querySelector('[data-done-toggle]');
    if (checkbox) {
      checkbox.addEventListener('change', function () {
        const previous = !checkbox.checked;
        const done = checkbox.checked;
        updateCompleteState(item, done);
        setStatus(item, 'Saving...', 'saving');
        apiPatchWithRetry(endpointFor(item), {done_at: done ? new Date().toISOString() : null})
          .then(function () {
            setStatus(item, 'Saved', 'saved');
          })
          .catch(function () {
            checkbox.checked = previous;
            updateCompleteState(item, previous);
            setStatus(item, 'Save failed. Try again.', 'failed');
          });
      });
    }

    const edit = item.querySelector('[data-edit-item]');
    const save = item.querySelector('[data-save-item]');
    const cancel = item.querySelector('[data-cancel-edit]');
    const textarea = item.querySelector('[data-markdown-input]');
    const rendered = item.querySelector('[data-rendered-markdown]');
    if (!edit || !save || !cancel || !textarea || !rendered) { return; }

    let original = textarea.value;
    function setEditing(isEditing) {
      textarea.classList.toggle('hidden', !isEditing);
      save.classList.toggle('hidden', !isEditing);
      cancel.classList.toggle('hidden', !isEditing);
      edit.classList.toggle('hidden', isEditing);
      rendered.classList.toggle('hidden', isEditing);
      if (isEditing) { textarea.focus(); }
    }

    edit.addEventListener('click', function () {
      original = textarea.value;
      setStatus(item, '', 'saved');
      setEditing(true);
    });
    cancel.addEventListener('click', function () {
      textarea.value = original;
      setStatus(item, '', 'saved');
      setEditing(false);
    });
    save.addEventListener('click', function () {
      const value = textarea.value;
      setStatus(item, 'Saving...', 'saving');
      apiPatchWithRetry(endpointFor(item), {description: value})
        .then(function () {
          original = value;
          rendered.innerHTML = renderMarkdown(value);
          setEditing(false);
          setStatus(item, 'Saved', 'saved');
        })
        .catch(function () {
          setStatus(item, 'Save failed. Try again.', 'failed');
        });
    });
  });
})();

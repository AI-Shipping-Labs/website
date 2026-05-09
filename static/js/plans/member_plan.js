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

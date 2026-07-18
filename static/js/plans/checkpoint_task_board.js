/*
 * Shared sprint-plan checkpoint task board.
 *
 * Binds the canonical data-checkpoint-* hooks emitted by
 * templates/plans/_checkpoint_card.html. Member workspace and Studio both
 * use this for completion, inline edit, drag/drop, keyboard movement,
 * reconciliation, optimistic rollback, and per-week unfinished moves.
 */
(function () {
  'use strict';

  function getCookie(name) {
    const prefix = name + '=';
    return document.cookie.split(';').map(function (cookie) {
      return cookie.trim();
    }).find(function (cookie) {
      return cookie.startsWith(prefix);
    })?.substring(prefix.length) || '';
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderInline(text) {
    let html = escapeHtml(text);
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|mailto:[^)\s]+|\/[^)\s]*|#[^)\s]*)\)/g, function (_match, label, url) {
      return '<a href="' + escapeHtml(url) + '" rel="noopener noreferrer">' + label + '</a>';
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

  function createBoard(root, options) {
    options = options || {};
    const apiBase = options.apiBase || root.dataset.apiBase || '/api/';
    const apiToken = options.apiToken || root.dataset.apiToken || '';
    const retryDelayMs = options.retryDelayMs === undefined ? 1000 : options.retryDelayMs;
    const editable = options.editable !== false;
    const allowDelete = options.allowDelete === true;
    const showToast = options.onToast || function () {};
    const onSaving = options.onSaving || function () {};
    const onSaved = options.onSaved || function () {};
    const onFailed = options.onFailed || function () {};
    const onProgressChange = options.onProgressChange || function () {};
    const cardSelector = '[data-checkpoint-card]';
    const listSelector = '[data-checkpoint-list], [data-testid="checkpoint-list"]';
    const boundCards = new WeakSet();

    function listForWeek(weekId) {
      return root.querySelector(
        '[data-checkpoint-list][data-week-id="' + weekId + '"], '
        + '[data-testid="checkpoint-list"][data-week-id="' + weekId + '"]'
      );
    }

    function cardsInWeek(weekId) {
      const list = listForWeek(weekId);
      if (!list) { return []; }
      return Array.from(list.querySelectorAll(cardSelector));
    }

    function allLists() {
      return Array.from(root.querySelectorAll(listSelector));
    }

    function allCards() {
      return Array.from(root.querySelectorAll(cardSelector));
    }

    function request(method, path, body) {
      onSaving();
      const headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
      };
      if (apiToken) {
        headers.Authorization = 'Token ' + apiToken;
      } else {
        headers['X-CSRFToken'] = getCookie('csrftoken');
        headers['X-Requested-With'] = 'XMLHttpRequest';
      }

      const init = {
        method: method,
        headers: headers,
        credentials: 'same-origin',
      };
      if (body !== undefined && body !== null) {
        init.body = JSON.stringify(body);
      }

      return fetch(apiBase + path.replace(/^\//, ''), init).then(function (resp) {
        const isJson = (resp.headers.get('content-type') || '').indexOf('application/json') !== -1;
        const hasBody = resp.status !== 204 && resp.status !== 205;
        const parse = isJson && hasBody ? resp.json() : Promise.resolve(null);
        return parse.then(function (data) {
          if (resp.ok) {
            onSaved();
            return { ok: true, status: resp.status, data: data };
          }
          return {
            ok: false,
            status: resp.status,
            data: data,
            code: (data && data.code) || 'http_' + resp.status,
            message: (data && data.error) || 'Request failed: ' + resp.status,
          };
        });
      }).catch(function (err) {
        return {
          ok: false,
          status: 0,
          data: null,
          code: 'network_error',
          message: String(err),
        };
      });
    }

    function writeWithRetry(method, path, body, rollback, failureMessage) {
      return request(method, path, body).then(function (result) {
        if (result.ok) { return result; }
        return new Promise(function (resolve) {
          setTimeout(function () {
            request(method, path, body).then(function (retry) {
              if (retry.ok) {
                resolve(retry);
                return;
              }
              if (rollback) { rollback(retry); }
              onFailed(retry.message);
              showToast(
                failureMessage
                || "Couldn't save task change. Your edit was reverted."
              );
              resolve(retry);
            });
          }, retryDelayMs);
        });
      });
    }

    function setCardStatus(card, text, state) {
      const status = card.querySelector('[data-checkpoint-status], [data-save-status]');
      if (!status) { return; }
      status.textContent = text || '';
      status.classList.remove('text-destructive', 'text-muted-foreground');
      status.classList.add(state === 'failed' ? 'text-destructive' : 'text-muted-foreground');
    }

    function updateProgress() {
      const cards = allCards();
      const done = cards.filter(function (card) {
        return card.dataset.done === 'true';
      }).length;
      onProgressChange(done, cards.length);
    }

    function updateDoneVisual(card, isDone) {
      card.dataset.done = isDone ? 'true' : 'false';
      card.classList.toggle('is-complete', isDone);
      const checkbox = card.querySelector('[data-checkpoint-done-toggle]');
      if (checkbox) { checkbox.checked = isDone; }
      const text = card.querySelector('[data-checkpoint-text]');
      if (text) {
        text.classList.toggle('line-through', isDone);
        text.classList.toggle('text-muted-foreground', isDone);
        text.classList.toggle('text-foreground', !isDone);
      }
      updateProgress();
    }

    function renumberWeek(weekId) {
      cardsInWeek(weekId).forEach(function (card, idx) {
        card.dataset.weekId = String(weekId);
        card.dataset.position = String(idx);
      });
    }

    function snapshotWeek(weekId) {
      return cardsInWeek(weekId).map(function (card) {
        return {
          id: parseInt(card.dataset.checkpointId, 10),
          weekId: parseInt(card.dataset.weekId, 10),
          position: parseInt(card.dataset.position || '0', 10),
        };
      });
    }

    function snapshotAll() {
      let snapshot = [];
      allLists().forEach(function (list) {
        snapshot = snapshot.concat(snapshotWeek(parseInt(list.dataset.weekId, 10)));
      });
      return snapshot;
    }

    function restoreSnapshot(snapshot) {
      const byWeek = {};
      snapshot.forEach(function (entry) {
        if (!byWeek[entry.weekId]) { byWeek[entry.weekId] = []; }
        byWeek[entry.weekId].push(entry);
      });
      Object.keys(byWeek).forEach(function (weekIdStr) {
        const weekId = parseInt(weekIdStr, 10);
        const list = listForWeek(weekId);
        if (!list) { return; }
        byWeek[weekIdStr].slice().sort(function (a, b) {
          return a.position - b.position;
        }).forEach(function (entry) {
          const card = root.querySelector(
            cardSelector + '[data-checkpoint-id="' + entry.id + '"]'
          );
          if (!card) { return; }
          list.appendChild(card);
          card.dataset.weekId = String(weekId);
          card.dataset.position = String(entry.position);
        });
        updateEmptyWeekHint(weekId);
      });
    }

    function reconcileWeek(weekId, ids) {
      const list = listForWeek(weekId);
      if (!list || !ids) { return; }
      const focusedId = document.activeElement && document.activeElement.dataset
        ? document.activeElement.dataset.checkpointId
        : null;
      ids.forEach(function (id, idx) {
        const card = root.querySelector(
          cardSelector + '[data-checkpoint-id="' + id + '"]'
        );
        if (!card) { return; }
        const alreadyHere = card.parentNode === list && list.children[idx] === card;
        if (!alreadyHere) {
          list.insertBefore(card, list.children[idx] || null);
          if (focusedId && String(focusedId) === String(id)) {
            card.focus();
          }
        }
        card.dataset.weekId = String(weekId);
        card.dataset.position = String(idx);
      });
      updateEmptyWeekHint(weekId);
    }

    function reconcileMoveEnvelope(data) {
      if (!data) { return; }
      if (data.source_week) {
        reconcileWeek(data.source_week.id, data.source_week.checkpoint_ids);
      }
      if (data.destination_week) {
        reconcileWeek(
          data.destination_week.id,
          data.destination_week.checkpoint_ids,
        );
      }
    }

    function moveCard(card, destWeekId, destPosition, snapshot) {
      return writeWithRetry(
        'POST',
        'checkpoints/' + card.dataset.checkpointId + '/move',
        { week_id: destWeekId, position: destPosition },
        function () {
          if (snapshot) { restoreSnapshot(snapshot); }
        },
        "Couldn't save change - task move was reverted."
      ).then(function (result) {
        if (result.ok) {
          reconcileMoveEnvelope(result.data);
        }
        return result;
      });
    }

    function updateEmptyWeekHint(weekId) {
      const list = listForWeek(weekId);
      if (!list) { return; }
      const card = list.closest('[data-testid="week-card"], [data-testid="plan-week"]');
      const hint = card
        ? card.querySelector('[data-testid="empty-week-hint"]')
        : null;
      if (!hint) { return; }
      const count = cardsInWeek(weekId).length;
      hint.classList.toggle('hidden', count > 0);
    }

    function setExistingEditorState(card, editing) {
      const input = card.querySelector('[data-checkpoint-edit-input], [data-markdown-input]');
      const save = card.querySelector('[data-checkpoint-save], [data-save-item]');
      const cancel = card.querySelector('[data-checkpoint-cancel], [data-cancel-edit]');
      const edit = card.querySelector('[data-checkpoint-edit], [data-edit-item]');
      const rendered = card.querySelector('[data-checkpoint-text]');
      if (!input || !save || !cancel || !rendered) { return false; }
      input.classList.toggle('hidden', !editing);
      save.classList.toggle('hidden', !editing);
      cancel.classList.toggle('hidden', !editing);
      if (edit) { edit.classList.toggle('hidden', editing); }
      rendered.classList.toggle('hidden', editing);
      card.dataset.editing = editing ? 'true' : 'false';
      if (editing) {
        input.focus();
        input.select();
      }
      return true;
    }

    function commitExistingEdit(card) {
      const input = card.querySelector('[data-checkpoint-edit-input], [data-markdown-input]');
      const rendered = card.querySelector('[data-checkpoint-text]');
      if (!input || !rendered) { return; }
      const prior = rendered.dataset.markdownSource || input.defaultValue || '';
      const priorHtml = rendered.innerHTML;
      const value = input.value.trim();
      if (!value) {
        input.value = prior;
        setExistingEditorState(card, false);
        setCardStatus(card, 'Enter a task or delete it instead.', 'failed');
        card.focus();
        return;
      }
      setCardStatus(card, 'Saving...', 'saving');
      writeWithRetry(
        'PATCH',
        'checkpoints/' + card.dataset.checkpointId,
        { description: value },
        function () {
          input.value = prior;
          rendered.innerHTML = priorHtml;
          rendered.dataset.markdownSource = prior;
        },
        "Couldn't save task text. Your edit was reverted."
      ).then(function (result) {
        if (result.ok) {
          const data = result.data || {};
          const description = data.description !== undefined ? data.description : value;
          input.value = description;
          input.defaultValue = description;
          rendered.innerHTML = data.description_html || renderMarkdown(description);
          rendered.dataset.markdownSource = description;
          setExistingEditorState(card, false);
          setCardStatus(card, 'Saved', 'saved');
          card.focus();
        } else {
          setExistingEditorState(card, false);
          setCardStatus(card, 'Save failed. Try again.', 'failed');
          card.focus();
        }
      });
    }

    function cancelExistingEdit(card) {
      const input = card.querySelector('[data-checkpoint-edit-input], [data-markdown-input]');
      const rendered = card.querySelector('[data-checkpoint-text]');
      if (input && rendered) {
        input.value = rendered.dataset.markdownSource || input.defaultValue || input.value;
      }
      setCardStatus(card, '', 'saved');
      setExistingEditorState(card, false);
      card.focus();
    }

    function enterInlineEdit(card) {
      if (!editable || card.dataset.editing === 'true') { return; }
      const existingInput = card.querySelector('[data-checkpoint-edit-input], [data-markdown-input]');
      if (existingInput) {
        existingInput.value = card.querySelector('[data-checkpoint-text]')?.dataset.markdownSource || existingInput.value;
        setCardStatus(card, '', 'saved');
        setExistingEditorState(card, true);
        return;
      }

      const textEl = card.querySelector('[data-checkpoint-text]');
      if (!textEl) { return; }
      card.dataset.editing = 'true';
      const prior = textEl.dataset.markdownSource || textEl.textContent || '';
      const priorHtml = textEl.innerHTML;
      const ta = document.createElement('textarea');
      ta.value = prior;
      ta.className = 'flex-1 bg-transparent border-0 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-accent rounded';
      ta.setAttribute('data-testid', 'checkpoint-edit-textarea');
      ta.setAttribute('data-checkpoint-edit-input', '');
      textEl.replaceWith(ta);
      ta.focus();
      ta.select();

      let finished = false;
      const isDraft = card.dataset.checkpointDraft === 'true';

      function removeDraft() {
        const weekId = parseInt(card.dataset.weekId, 10);
        const weekCard = card.closest(
          '[data-testid="week-card"], [data-testid="plan-week"]'
        );
        const addButton = weekCard
          ? weekCard.querySelector('[data-testid="add-checkpoint"]')
          : null;
        card.remove();
        updateEmptyWeekHint(weekId);
        updateProgress();
        if (addButton) { addButton.focus(); }
      }

      function finish(save) {
        if (finished) { return; }
        const value = ta.value.trim();
        if (isDraft && (!save || !value)) {
          finished = true;
          removeDraft();
          return;
        }
        if (!isDraft && save && !value) {
          finished = true;
          ta.replaceWith(textEl);
          card.dataset.editing = 'false';
          bindCard(card);
          setCardStatus(card, 'Enter a task or delete it instead.', 'failed');
          showToast('Enter a task or delete it instead.');
          card.focus();
          return;
        }
        finished = true;
        const nextText = textEl;
        nextText.innerHTML = save ? renderMarkdown(value || prior) : priorHtml;
        nextText.dataset.markdownSource = save ? (value || prior) : prior;
        ta.replaceWith(nextText);
        card.dataset.editing = 'false';
        bindCard(card);
        if (isDraft && save) {
          request(
            'POST',
            'weeks/' + card.dataset.weekId + '/checkpoints',
            { description: value }
          ).then(function (result) {
            if (!result.ok || !result.data || !result.data.id) {
              showToast("Couldn't add checkpoint. The draft was kept locally.");
              card.dataset.editing = 'false';
              card.dataset.checkpointDraft = 'true';
              bindCard(card);
              card.focus();
              return;
            }
            card.dataset.checkpointDraft = 'false';
            card.dataset.checkpointId = String(result.data.id);
            card.dataset.itemId = String(result.data.id);
            card.dataset.position = String(result.data.position);
            nextText.innerHTML = result.data.description_html || renderMarkdown(value);
            nextText.dataset.markdownSource = result.data.description || value;
            bindCard(card);
            bindSortable();
            updateProgress();
          });
        } else if (save && value && value !== prior) {
          writeWithRetry(
            'PATCH',
            'checkpoints/' + card.dataset.checkpointId,
            { description: value },
            function () {
              nextText.innerHTML = priorHtml;
              nextText.dataset.markdownSource = prior;
            },
            "Couldn't save task text. Your edit was reverted."
          ).then(function (result) {
            if (result.ok && result.data) {
              nextText.innerHTML = result.data.description_html || renderMarkdown(value);
              nextText.dataset.markdownSource = result.data.description || value;
            }
          });
        }
        card.focus();
      }

      ta.addEventListener('blur', function () { finish(true); });
      ta.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          e.preventDefault();
          finish(false);
        } else if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          ta.blur();
        }
      });
    }

    function keyboardTarget(card, direction, crossWeek) {
      const lists = allLists();
      const currentList = card.parentNode;
      const currentWeekId = parseInt(currentList.dataset.weekId, 10);
      if (!crossWeek) {
        const siblings = cardsInWeek(currentWeekId);
        const index = siblings.indexOf(card);
        const position = index + direction;
        if (position < 0 || position >= siblings.length) { return null; }
        return { weekId: currentWeekId, position: position };
      }

      const currentIndex = lists.indexOf(currentList);
      const targetList = lists[currentIndex + direction];
      if (!targetList) { return null; }
      return {
        weekId: parseInt(targetList.dataset.weekId, 10),
        position: direction > 0 ? 0 : cardsInWeek(targetList.dataset.weekId).length,
      };
    }

    function keyboardMove(card, direction, crossWeek) {
      const target = keyboardTarget(card, direction, crossWeek);
      if (!target) { return; }
      const snapshot = snapshotAll();
      const fromWeekId = parseInt(card.parentNode.dataset.weekId, 10);
      const destList = listForWeek(target.weekId);
      if (!destList) { return; }
      if (crossWeek) {
        if (direction > 0) {
          destList.insertBefore(card, destList.firstChild);
        } else {
          destList.appendChild(card);
        }
      } else {
        const siblings = cardsInWeek(target.weekId);
        if (direction < 0) {
          destList.insertBefore(card, siblings[target.position]);
        } else {
          destList.insertBefore(card, siblings[target.position].nextSibling);
        }
      }
      card.dataset.weekId = String(target.weekId);
      renumberWeek(fromWeekId);
      renumberWeek(target.weekId);
      updateEmptyWeekHint(fromWeekId);
      updateEmptyWeekHint(target.weekId);
      card.focus();
      moveCard(card, target.weekId, target.position, snapshot);
    }

    function showInlineConfirm(card, onConfirm) {
      const original = card.innerHTML;
      card.innerHTML = '';
      card.dataset.confirming = 'true';
      const label = document.createElement('span');
      label.className = 'flex-1 text-sm text-foreground';
      label.textContent = 'Delete?';
      const yes = document.createElement('button');
      yes.type = 'button';
      yes.className = 'text-xs text-destructive hover:underline';
      yes.textContent = 'Yes';
      yes.setAttribute('data-testid', 'checkpoint-delete-confirm');
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'text-xs text-muted-foreground hover:underline';
      cancel.textContent = 'Cancel';
      cancel.setAttribute('data-testid', 'checkpoint-delete-cancel');
      card.appendChild(label);
      card.appendChild(yes);
      card.appendChild(cancel);

      function restoreConfirm() {
        card.innerHTML = original;
        card.dataset.confirming = 'false';
        boundCards.delete(card);
        bindCard(card);
      }

      yes.addEventListener('click', function () {
        onConfirm(restoreConfirm);
      });
      cancel.addEventListener('click', function () {
        restoreConfirm();
        card.focus();
      });
    }

    function deleteCard(card) {
      if (!allowDelete) { return; }
      const id = card.dataset.checkpointId;
      showInlineConfirm(card, function (restoreConfirm) {
        const parent = card.parentNode;
        const sibling = card.nextSibling;
        const weekId = parseInt(parent.dataset.weekId, 10);
        card.remove();
        renumberWeek(weekId);
        updateEmptyWeekHint(weekId);
        writeWithRetry(
          'DELETE',
          'checkpoints/' + id,
          null,
          function () {
            parent.insertBefore(card, sibling);
            restoreConfirm();
            renumberWeek(weekId);
            updateEmptyWeekHint(weekId);
            card.focus();
          },
          "Couldn't delete task. It was restored."
        );
      });
    }

    function bindCard(card) {
      if (!card || boundCards.has(card)) { return; }
      boundCards.add(card);
      const checkbox = card.querySelector('[data-checkpoint-done-toggle]');
      const text = card.querySelector('[data-checkpoint-text]');
      const trigger = card.querySelector('[data-checkpoint-edit-trigger]');
      const input = card.querySelector('[data-checkpoint-edit-input], [data-markdown-input]');
      const edit = card.querySelector('[data-checkpoint-edit], [data-edit-item]');
      const save = card.querySelector('[data-checkpoint-save], [data-save-item]');
      const cancel = card.querySelector('[data-checkpoint-cancel], [data-cancel-edit]');
      const del = card.querySelector('[data-checkpoint-delete]');

      if (checkbox) {
        checkbox.addEventListener('change', function () {
          const next = checkbox.checked;
          const prior = card.dataset.done === 'true';
          updateDoneVisual(card, next);
          setCardStatus(card, 'Saving...', 'saving');
          writeWithRetry(
            'PATCH',
            'checkpoints/' + card.dataset.checkpointId,
            { done_at: next ? new Date().toISOString() : null },
            function () {
              updateDoneVisual(card, prior);
            },
            "Couldn't save task completion. Your change was reverted."
          ).then(function (result) {
            if (result.ok) {
              setCardStatus(card, 'Saved', 'saved');
            } else {
              setCardStatus(card, 'Save failed. Try again.', 'failed');
            }
          });
        });
      }

      if (text) {
        text.addEventListener('click', function (e) {
          if (e.target.closest && e.target.closest('a, button, input, textarea, label')) {
            return;
          }
          enterInlineEdit(card);
        });
      }
      if (trigger) {
        trigger.addEventListener('click', function (e) {
          if (e.target.closest && e.target.closest('a, button, input, textarea, label')) {
            return;
          }
          enterInlineEdit(card);
        });
      }
      if (edit) {
        edit.addEventListener('click', function () {
          enterInlineEdit(card);
        });
      }
      if (save) {
        save.addEventListener('click', function () {
          commitExistingEdit(card);
        });
      }
      if (cancel) {
        cancel.addEventListener('click', function () {
          cancelExistingEdit(card);
        });
      }
      if (input) {
        input.addEventListener('keydown', function (e) {
          if (e.key === 'Escape') {
            e.preventDefault();
            cancelExistingEdit(card);
          }
        });
      }
      if (del) {
        del.addEventListener('click', function (e) {
          e.preventDefault();
          deleteCard(card);
        });
      }

      card.addEventListener('keydown', function (e) {
        // Inline editors and delete-confirmation buttons own their keyboard
        // events. In particular, Enter on the focused "Yes" button must keep
        // its native button activation instead of bubbling into card edit.
        if (
          !editable
          || card.dataset.editing === 'true'
          || card.dataset.confirming === 'true'
        ) { return; }
        if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
          e.preventDefault();
          keyboardMove(card, e.key === 'ArrowUp' ? -1 : 1, e.altKey);
        } else if (e.key === 'Enter' || e.key === 'F2') {
          e.preventDefault();
          enterInlineEdit(card);
        } else if (e.key === 'Delete' && allowDelete) {
          e.preventDefault();
          deleteCard(card);
        }
      });
    }

    function nextListAfter(sourceWeekId) {
      const lists = allLists();
      const index = lists.findIndex(function (list) {
        return parseInt(list.dataset.weekId, 10) === parseInt(sourceWeekId, 10);
      });
      if (index < 0 || index + 1 >= lists.length) { return null; }
      return lists[index + 1];
    }

    function moveIncompleteToNextWeek(sourceWeekId) {
      const sourceList = listForWeek(sourceWeekId);
      const destList = nextListAfter(sourceWeekId);
      if (!sourceList || !destList) { return Promise.resolve({ ok: true }); }
      const destWeekId = parseInt(destList.dataset.weekId, 10);
      const incomplete = cardsInWeek(sourceWeekId).filter(function (card) {
        return card.dataset.done !== 'true';
      });
      if (incomplete.length === 0) { return Promise.resolve({ ok: true }); }

      const snapshot = snapshotAll();
      incomplete.forEach(function (card, idx) {
        destList.insertBefore(card, destList.children[idx] || null);
        card.dataset.weekId = String(destWeekId);
      });
      renumberWeek(sourceWeekId);
      renumberWeek(destWeekId);
      updateEmptyWeekHint(sourceWeekId);
      updateEmptyWeekHint(destWeekId);

      function step(index) {
        if (index >= incomplete.length) {
          return Promise.resolve({ ok: true });
        }
        const card = incomplete[index];
        return writeWithRetry(
          'POST',
          'checkpoints/' + card.dataset.checkpointId + '/move',
          { week_id: destWeekId, position: index },
          function () {
            restoreSnapshot(snapshot);
          },
          "Couldn't save change - unfinished task move was reverted."
        ).then(function (result) {
          if (!result.ok) { return result; }
          reconcileMoveEnvelope(result.data);
          return step(index + 1);
        });
      }

      return step(0).then(function (result) {
        updateEmptyWeekHint(sourceWeekId);
        updateEmptyWeekHint(destWeekId);
        return result;
      });
    }

    function bindBulkButtons() {
      root.querySelectorAll('[data-checkpoint-move-incomplete], [data-testid="move-incomplete-to-next-week"]').forEach(function (button) {
        if (button.dataset.checkpointBulkBound === 'true') { return; }
        button.dataset.checkpointBulkBound = 'true';
        button.addEventListener('click', function (e) {
          e.preventDefault();
          moveIncompleteToNextWeek(parseInt(button.dataset.weekId, 10));
        });
      });
    }

    function bindSortable() {
      if (!editable || typeof window.Sortable === 'undefined') { return; }
      allLists().forEach(function (list) {
        if (list.dataset.checkpointSortableBound === 'true') { return; }
        list.dataset.checkpointSortableBound = 'true';
        window.Sortable.create(list, {
          group: 'checkpoints',
          handle: '[data-checkpoint-drag-handle], .plan-editor-drag-handle',
          draggable: cardSelector,
          animation: 150,
          onStart: function () {
            list.dataset.dragSnapshot = JSON.stringify(snapshotAll());
          },
          onEnd: function (evt) {
            const card = evt.item;
            const destList = evt.to;
            const destWeekId = parseInt(destList.dataset.weekId, 10);
            const fromWeekId = parseInt(evt.from.dataset.weekId, 10);
            const snapshot = evt.from.dataset.dragSnapshot
              ? JSON.parse(evt.from.dataset.dragSnapshot)
              : snapshotAll();
            card.dataset.weekId = String(destWeekId);
            renumberWeek(destWeekId);
            if (fromWeekId !== destWeekId) {
              renumberWeek(fromWeekId);
            }
            updateEmptyWeekHint(fromWeekId);
            updateEmptyWeekHint(destWeekId);
            moveCard(card, destWeekId, evt.newIndex, snapshot);
          },
        });
      });
    }

    allCards().forEach(bindCard);
    bindBulkButtons();
    bindSortable();
    updateProgress();

    return {
      bindCard: bindCard,
      bindBulkButtons: bindBulkButtons,
      bindSortable: bindSortable,
      enterInlineEdit: enterInlineEdit,
      moveIncompleteToNextWeek: moveIncompleteToNextWeek,
      reconcileMoveEnvelope: reconcileMoveEnvelope,
      renderMarkdown: renderMarkdown,
      request: request,
    };
  }

  window.SprintPlanTaskBoard = {
    create: createBoard,
    escapeHtml: escapeHtml,
    renderMarkdown: renderMarkdown,
  };
})();

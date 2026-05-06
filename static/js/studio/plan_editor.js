/*
 * Studio drag-and-drop plan editor (issue #434).
 *
 * Reads its bootstrap state from the in-page <script id="plan-editor-data">
 * JSON blob, wires SortableJS to the checkpoint / resource / deliverable /
 * next-step lists, and routes every write through the JSON API from #433.
 *
 * Save semantics (from the spec):
 *   - Text edits: 800 ms debounce per field, OR on blur, whichever is first.
 *   - Drag, toggle, add, delete: immediate, no debounce.
 *   - Failures revert the optimistic UI and surface a toast; one retry is
 *     scheduled 1 s later before giving up and leaving the indicator red.
 *
 * Keyboard accessibility (non-negotiable):
 *   - ArrowUp / ArrowDown: move within the same list.
 *   - Alt+ArrowUp / Alt+ArrowDown: cross-week move for checkpoints.
 *   - Enter / F2: enter inline edit on a chip; Escape cancels.
 *   - Delete (when not editing): same delete confirm flow as the X button.
 *   - Focus is preserved across moves (the same chip stays focused at its
 *     new position).
 *
 * The keyboard path hits the same /api/checkpoints/<id>/move endpoint as
 * the drag path -- there is exactly one save code path.
 */

(function () {
  'use strict';

  // ---------- bootstrap ----------

  const root = document.getElementById('plan-editor');
  if (!root) {
    return;
  }

  const dataNode = document.getElementById('plan-editor-data');
  if (!dataNode) {
    console.error('plan-editor: missing data node');
    return;
  }

  let plan;
  try {
    plan = JSON.parse(dataNode.textContent);
  } catch (e) {
    console.error('plan-editor: failed to parse bootstrap JSON', e);
    return;
  }

  const apiBase = root.dataset.apiBase || '/api/';
  const apiToken = root.dataset.apiToken || '';
  const planId = parseInt(root.dataset.planId, 10);

  if (!apiToken) {
    console.warn('plan-editor: missing API token; writes will fail with 401');
  }

  // ---------- saved/saving indicator ----------

  const indicator = document.querySelector('[data-testid="save-indicator"]');
  const indicatorDot = document.querySelector('[data-testid="save-indicator-dot"]');
  const indicatorLabel = document.querySelector('[data-testid="save-indicator-label"]');
  const toastEl = document.getElementById('plan-editor-toast');

  function setIndicator(state, errorText) {
    if (!indicator) { return; }
    indicator.dataset.state = state;
    if (state === 'saving') {
      indicatorDot.className = 'w-2 h-2 rounded-full bg-amber-500';
      indicatorLabel.textContent = 'Saving…';
      indicator.removeAttribute('title');
    } else if (state === 'saved') {
      indicatorDot.className = 'w-2 h-2 rounded-full bg-emerald-500';
      indicatorLabel.textContent = 'Saved';
      indicator.removeAttribute('title');
    } else if (state === 'failed') {
      indicatorDot.className = 'w-2 h-2 rounded-full bg-red-500';
      indicatorLabel.textContent = 'Save failed — retrying';
      if (errorText) {
        indicator.title = errorText;
      }
    }
  }

  let toastTimer = null;
  function showToast(message) {
    if (!toastEl) { return; }
    toastEl.textContent = message;
    toastEl.classList.remove('hidden');
    if (toastTimer) { clearTimeout(toastTimer); }
    toastTimer = setTimeout(function () {
      toastEl.classList.add('hidden');
    }, 4000);
  }

  // ---------- API helper ----------

  let inflight = 0;

  function apiCall(method, path, body) {
    inflight += 1;
    setIndicator('saving');

    const url = apiBase + path.replace(/^\//, '');
    const init = {
      method: method,
      headers: {
        'Authorization': 'Token ' + apiToken,
        'Content-Type': 'application/json',
      },
    };
    if (body !== undefined && body !== null) {
      init.body = JSON.stringify(body);
    }

    return fetch(url, init).then(function (resp) {
      // 204 No Content has no body per RFC 7230 -- browsers strip it
      // even when the server set Content-Type: application/json. Calling
      // resp.json() on a 204 rejects with a SyntaxError, which would
      // bubble into our .catch as a phantom network_error and trigger a
      // spurious retry of the (already successful) write.
      const isJson = (resp.headers.get('content-type') || '').indexOf('application/json') !== -1;
      const hasBody = resp.status !== 204 && resp.status !== 205;
      const parse = (isJson && hasBody) ? resp.json() : Promise.resolve(null);
      return parse.then(function (data) {
        inflight -= 1;
        if (resp.ok) {
          if (inflight === 0) { setIndicator('saved'); }
          return { ok: true, status: resp.status, data: data };
        }
        const code = (data && data.code) || ('http_' + resp.status);
        const message = (data && data.error) || ('Request failed: ' + resp.status);
        return { ok: false, status: resp.status, data: data, code: code, message: message };
      });
    }).catch(function (err) {
      inflight -= 1;
      return { ok: false, status: 0, code: 'network_error', message: String(err) };
    });
  }

  function apiCallWithRevert(method, path, body, revert) {
    return apiCall(method, path, body).then(function (result) {
      if (result.ok) { return result; }
      // First failure: schedule one retry after 1 second.
      return new Promise(function (resolve) {
        setTimeout(function () {
          apiCall(method, path, body).then(function (retry) {
            if (retry.ok) {
              resolve(retry);
            } else {
              if (revert) { revert(retry); }
              setIndicator('failed', retry.message);
              showToast("Couldn't save change — your edit was reverted (" + retry.code + ').');
              resolve(retry);
            }
          });
        }, 1000);
      });
    });
  }

  // ---------- text-field debounce ----------

  const debounceTimers = new WeakMap();
  const fieldPriorValues = new WeakMap();
  const DEBOUNCE_MS = 800;

  function captureInitialValue(el) {
    if (!fieldPriorValues.has(el)) {
      fieldPriorValues.set(el, el.value);
    }
  }

  function flushTextField(el, sendFn) {
    const timer = debounceTimers.get(el);
    if (timer) { clearTimeout(timer); debounceTimers.delete(el); }
    const value = el.value;
    const prior = fieldPriorValues.get(el);
    if (value === prior) { return; }
    sendFn(value, prior).then(function (result) {
      if (result && result.ok) {
        fieldPriorValues.set(el, value);
      } else {
        // Revert the textarea to the prior value so the user sees the
        // failure rather than thinking their edit landed.
        el.value = prior;
      }
    });
  }

  function bindDebouncedField(el, sendFn) {
    captureInitialValue(el);
    el.addEventListener('input', function () {
      const timer = debounceTimers.get(el);
      if (timer) { clearTimeout(timer); }
      debounceTimers.set(el, setTimeout(function () {
        flushTextField(el, sendFn);
      }, DEBOUNCE_MS));
    });
    el.addEventListener('blur', function () {
      flushTextField(el, sendFn);
    });
  }

  // ---------- summary fields ----------

  const summaryFields = root.querySelectorAll('.plan-editor-textarea[data-field]');
  summaryFields.forEach(function (el) {
    const field = el.dataset.field;
    bindDebouncedField(el, function (value) {
      const body = {};
      body[field] = value;
      // ``focus_main`` lives on the plan, but is also a textarea -- the
      // backend accepts both flat and nested patches per #433.
      return apiCallWithRevert('PATCH', 'plans/' + planId, body);
    });
  });

  // ---------- checkpoint chips ----------

  function checkpointChip(li) {
    return {
      id: parseInt(li.dataset.checkpointId, 10),
      weekId: parseInt(li.dataset.weekId, 10),
      el: li,
    };
  }

  function getCheckpointSiblings(weekId) {
    const list = root.querySelector(
      '[data-testid="checkpoint-list"][data-week-id="' + weekId + '"]'
    );
    if (!list) { return []; }
    return Array.from(list.querySelectorAll('[data-testid="checkpoint-chip"]'));
  }

  function renumberCheckpoints(weekId) {
    const siblings = getCheckpointSiblings(weekId);
    siblings.forEach(function (sib, idx) {
      sib.dataset.position = String(idx);
      sib.dataset.weekId = String(weekId);
    });
  }

  function snapshotCheckpointPositions(weekId) {
    return getCheckpointSiblings(weekId).map(function (li) {
      return {
        id: parseInt(li.dataset.checkpointId, 10),
        weekId: parseInt(li.dataset.weekId, 10),
        position: parseInt(li.dataset.position, 10),
      };
    });
  }

  function restoreCheckpointSnapshot(snapshot) {
    // Group by source week; reinsert at recorded position.
    const byWeek = {};
    snapshot.forEach(function (entry) {
      if (!byWeek[entry.weekId]) { byWeek[entry.weekId] = []; }
      byWeek[entry.weekId].push(entry);
    });
    Object.keys(byWeek).forEach(function (weekIdStr) {
      const weekId = parseInt(weekIdStr, 10);
      const list = root.querySelector(
        '[data-testid="checkpoint-list"][data-week-id="' + weekId + '"]'
      );
      if (!list) { return; }
      const entries = byWeek[weekIdStr].slice().sort(function (a, b) {
        return a.position - b.position;
      });
      entries.forEach(function (entry) {
        // Find the chip wherever it currently is; could be in another week.
        const chip = root.querySelector(
          '[data-testid="checkpoint-chip"][data-checkpoint-id="' + entry.id + '"]'
        );
        if (chip) {
          list.appendChild(chip);
          chip.dataset.weekId = String(weekId);
          chip.dataset.position = String(entry.position);
        }
      });
    });
  }

  // Move a checkpoint via the API. Used by both drag and keyboard paths.
  function moveCheckpoint(chip, destWeekId, destPosition, snapshot) {
    return apiCall('POST', 'checkpoints/' + chip.dataset.checkpointId + '/move', {
      week_id: destWeekId,
      position: destPosition,
    }).then(function (result) {
      if (result.ok) {
        // Update DOM positions from the canonical envelope so optimistic
        // reorder reconciles with the server's contiguous numbering.
        if (result.data && result.data.source_week) {
          renumberCheckpointsFromIds(
            result.data.source_week.id,
            result.data.source_week.checkpoint_ids,
          );
        }
        if (result.data && result.data.destination_week) {
          renumberCheckpointsFromIds(
            result.data.destination_week.id,
            result.data.destination_week.checkpoint_ids,
          );
        }
        return result;
      }
      // Failure: revert from snapshot, retry once via the wrapper would
      // duplicate the failed write, so we trip the indicator directly.
      if (snapshot) { restoreCheckpointSnapshot(snapshot); }
      setIndicator('failed', result.message);
      showToast("Couldn't save change — your edit was reverted (" + result.code + ').');
      return result;
    });
  }

  function renumberCheckpointsFromIds(weekId, ids) {
    const list = root.querySelector(
      '[data-testid="checkpoint-list"][data-week-id="' + weekId + '"]'
    );
    if (!list || !ids) { return; }
    // Preserve focus across the reconciliation. ``appendChild`` of an
    // already-attached node detaches and re-attaches it, which blurs the
    // active element. Skip the DOM move when the chip is already in the
    // right slot so a successful drag/keyboard reorder doesn't
    // immediately lose focus once the API echo comes back.
    const focusedId = document.activeElement
      && document.activeElement.dataset
      ? document.activeElement.dataset.checkpointId
      : null;
    ids.forEach(function (id, idx) {
      const chip = root.querySelector(
        '[data-testid="checkpoint-chip"][data-checkpoint-id="' + id + '"]'
      );
      if (!chip) { return; }
      const existingChildren = list.children;
      const alreadyHere = (
        chip.parentNode === list && existingChildren[idx] === chip
      );
      if (!alreadyHere) {
        // Insert at the target index without using appendChild so the
        // ordering is exact; insertBefore against the current
        // child-at-index is still a detach+reattach in the spec, so we
        // explicitly re-focus the chip if it was the active element.
        const ref = existingChildren[idx] || null;
        list.insertBefore(chip, ref);
        if (focusedId && String(focusedId) === String(id)) {
          chip.focus();
        }
      }
      chip.dataset.weekId = String(weekId);
      chip.dataset.position = String(idx);
    });
  }

  // ---------- SortableJS wiring (drag) ----------

  if (typeof window.Sortable !== 'undefined') {
    const checkpointLists = root.querySelectorAll('.plan-editor-checkpoint-list');
    let dragSnapshot = null;
    checkpointLists.forEach(function (list) {
      window.Sortable.create(list, {
        group: 'checkpoints',
        handle: '.plan-editor-drag-handle',
        animation: 150,
        onStart: function () {
          // Snapshot the global checkpoint state across every week so a
          // failed move can restore exact positions.
          dragSnapshot = [];
          root.querySelectorAll(
            '[data-testid="checkpoint-list"]'
          ).forEach(function (l) {
            const wid = parseInt(l.dataset.weekId, 10);
            dragSnapshot = dragSnapshot.concat(snapshotCheckpointPositions(wid));
          });
        },
        onEnd: function (evt) {
          const chip = evt.item;
          const destList = evt.to;
          const destWeekId = parseInt(destList.dataset.weekId, 10);
          const destPosition = evt.newIndex;
          // Update local state optimistically before the API call.
          chip.dataset.weekId = String(destWeekId);
          renumberCheckpoints(destWeekId);
          if (evt.from !== evt.to) {
            renumberCheckpoints(parseInt(evt.from.dataset.weekId, 10));
          }
          moveCheckpoint(chip, destWeekId, destPosition, dragSnapshot);
        },
      });
    });

    // Resources reorder within their panel only (different group name).
    const resourceList = root.querySelector('.plan-editor-resource-list');
    if (resourceList) {
      window.Sortable.create(resourceList, {
        group: 'resources',
        animation: 150,
        onEnd: function (evt) {
          const li = evt.item;
          const id = parseInt(li.dataset.resourceId, 10);
          apiCallWithRevert('PATCH', 'resources/' + id, {
            position: evt.newIndex,
          });
        },
      });
    }
    const deliverableList = root.querySelector('.plan-editor-deliverable-list');
    if (deliverableList) {
      window.Sortable.create(deliverableList, {
        group: 'deliverables',
        animation: 150,
        onEnd: function (evt) {
          const li = evt.item;
          const id = parseInt(li.dataset.deliverableId, 10);
          apiCallWithRevert('PATCH', 'deliverables/' + id, {
            position: evt.newIndex,
          });
        },
      });
    }
    const nextStepList = root.querySelector('.plan-editor-next-step-list');
    if (nextStepList) {
      window.Sortable.create(nextStepList, {
        group: 'next-steps',
        animation: 150,
        onEnd: function (evt) {
          const li = evt.item;
          const id = parseInt(li.dataset.nextStepId, 10);
          apiCallWithRevert('PATCH', 'next-steps/' + id, {
            position: evt.newIndex,
          });
        },
      });
    }
  }

  // ---------- per-checkpoint UI: toggle done, delete, inline edit, keyboard ----------

  function updateCheckpointDoneVisual(chip, isDone) {
    chip.dataset.done = isDone ? 'true' : 'false';
    const text = chip.querySelector('[data-testid="checkpoint-text"]');
    if (text) {
      if (isDone) {
        text.classList.add('line-through', 'text-muted-foreground');
      } else {
        text.classList.remove('line-through', 'text-muted-foreground');
      }
    }
  }

  function bindCheckpointChip(chip) {
    const doneToggle = chip.querySelector('[data-testid="checkpoint-done-toggle"]');
    const deleteBtn = chip.querySelector('[data-testid="checkpoint-delete"]');
    const textEl = chip.querySelector('[data-testid="checkpoint-text"]');
    const id = parseInt(chip.dataset.checkpointId, 10);

    if (doneToggle) {
      doneToggle.addEventListener('change', function () {
        const isDone = doneToggle.checked;
        const prior = chip.dataset.done === 'true';
        updateCheckpointDoneVisual(chip, isDone);
        const body = { done_at: isDone ? new Date().toISOString() : null };
        apiCallWithRevert('PATCH', 'checkpoints/' + id, body, function () {
          updateCheckpointDoneVisual(chip, prior);
          doneToggle.checked = prior;
        });
      });
    }

    if (deleteBtn) {
      deleteBtn.addEventListener('click', function (e) {
        e.preventDefault();
        showInlineConfirm(chip, function () {
          // The chip is removed from the DOM optimistically; on failure
          // we re-insert at the prior position.
          const parent = chip.parentNode;
          const sibling = chip.nextSibling;
          chip.remove();
          apiCallWithRevert('DELETE', 'checkpoints/' + id, null, function () {
            parent.insertBefore(chip, sibling);
          });
          // Move focus to the next sibling, prev sibling, or add button.
          const next = sibling && sibling.nodeType === 1 ? sibling : null;
          if (next) {
            next.focus();
          } else {
            const list = parent;
            const last = list.lastElementChild;
            if (last) {
              last.focus();
            } else {
              const addBtn = list.parentNode.querySelector('[data-testid="add-checkpoint"]');
              if (addBtn) { addBtn.focus(); }
            }
          }
        });
      });
    }

    if (textEl) {
      textEl.addEventListener('click', function () {
        enterInlineEdit(chip, textEl, id);
      });
    }

    // Keyboard handlers: ArrowUp/Down, Alt+Arrow cross-week, Enter/F2,
    // Escape, Delete. Bound on the chip itself so focus is preserved.
    chip.addEventListener('keydown', function (e) {
      if (chip.dataset.editing === 'true') {
        // While editing, only Escape is handled here; everything else
        // is the textarea's own concern.
        return;
      }
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        const direction = e.key === 'ArrowUp' ? -1 : 1;
        if (e.altKey) {
          moveCheckpointAcrossWeeks(chip, direction);
        } else {
          moveCheckpointWithinWeek(chip, direction);
        }
        return;
      }
      if (e.key === 'Enter' || e.key === 'F2') {
        e.preventDefault();
        if (textEl) {
          enterInlineEdit(chip, textEl, id);
        }
        return;
      }
      if (e.key === 'Delete') {
        e.preventDefault();
        if (deleteBtn) { deleteBtn.click(); }
        return;
      }
    });
  }

  function moveCheckpointWithinWeek(chip, direction) {
    const list = chip.parentNode;
    const siblings = Array.from(
      list.querySelectorAll('[data-testid="checkpoint-chip"]')
    );
    const idx = siblings.indexOf(chip);
    const newIdx = idx + direction;
    if (newIdx < 0 || newIdx >= siblings.length) { return; }

    const snapshot = [];
    root.querySelectorAll('[data-testid="checkpoint-list"]').forEach(function (l) {
      snapshot.push.apply(
        snapshot,
        snapshotCheckpointPositions(parseInt(l.dataset.weekId, 10)),
      );
    });

    if (direction === -1) {
      list.insertBefore(chip, siblings[newIdx]);
    } else {
      const ref = siblings[newIdx].nextSibling;
      list.insertBefore(chip, ref);
    }
    const weekId = parseInt(list.dataset.weekId, 10);
    renumberCheckpoints(weekId);
    chip.focus();
    moveCheckpoint(chip, weekId, newIdx, snapshot);
  }

  function moveCheckpointAcrossWeeks(chip, direction) {
    const fromList = chip.parentNode;
    const fromWeekId = parseInt(fromList.dataset.weekId, 10);
    const allLists = Array.from(
      root.querySelectorAll('[data-testid="checkpoint-list"]')
    );
    const fromIdx = allLists.indexOf(fromList);
    const newWeekIdx = fromIdx + direction;
    if (newWeekIdx < 0 || newWeekIdx >= allLists.length) { return; }
    const destList = allLists[newWeekIdx];
    const destWeekId = parseInt(destList.dataset.weekId, 10);

    const snapshot = [];
    allLists.forEach(function (l) {
      snapshot.push.apply(
        snapshot,
        snapshotCheckpointPositions(parseInt(l.dataset.weekId, 10)),
      );
    });

    // Wrap from bottom of week N to top of week N+1; wrap top of N to
    // bottom of N-1 when going up.
    let destPosition;
    if (direction === 1) {
      // Bottom of source -> top of destination.
      destList.insertBefore(chip, destList.firstChild);
      destPosition = 0;
    } else {
      destList.appendChild(chip);
      destPosition = destList.querySelectorAll(
        '[data-testid="checkpoint-chip"]'
      ).length - 1;
    }
    chip.dataset.weekId = String(destWeekId);
    renumberCheckpoints(fromWeekId);
    renumberCheckpoints(destWeekId);
    chip.focus();
    moveCheckpoint(chip, destWeekId, destPosition, snapshot);
  }

  function enterInlineEdit(chip, textEl, id) {
    if (chip.dataset.editing === 'true') { return; }
    chip.dataset.editing = 'true';
    const prior = textEl.textContent;
    const ta = document.createElement('textarea');
    ta.value = prior;
    ta.className = 'flex-1 bg-transparent border-0 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-accent rounded';
    ta.setAttribute('data-testid', 'checkpoint-edit-textarea');
    textEl.replaceWith(ta);
    ta.focus();
    ta.select();

    function commit() {
      const value = ta.value.trim();
      const newSpan = textEl;
      newSpan.textContent = value || prior;
      ta.replaceWith(newSpan);
      chip.dataset.editing = 'false';
      if (value && value !== prior) {
        apiCallWithRevert('PATCH', 'checkpoints/' + id, {
          description: value,
        }, function () {
          newSpan.textContent = prior;
        });
      }
      chip.focus();
    }

    function cancel() {
      const newSpan = textEl;
      newSpan.textContent = prior;
      ta.replaceWith(newSpan);
      chip.dataset.editing = 'false';
      chip.focus();
    }

    ta.addEventListener('blur', commit);
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        cancel();
      } else if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        ta.blur();
      }
    });
  }

  function showInlineConfirm(chip, onConfirm) {
    // Render Yes/Cancel inline on the chip so there's no modal.
    const original = chip.innerHTML;
    chip.innerHTML = '';
    chip.dataset.confirming = 'true';
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
    chip.appendChild(label);
    chip.appendChild(yes);
    chip.appendChild(cancel);

    yes.addEventListener('click', function () {
      onConfirm();
    });
    cancel.addEventListener('click', function () {
      chip.innerHTML = original;
      chip.dataset.confirming = 'false';
      bindCheckpointChip(chip);
      chip.focus();
    });
  }

  // Wire every existing chip on first paint. New chips wired in addCheckpoint.
  root.querySelectorAll('[data-testid="checkpoint-chip"]').forEach(bindCheckpointChip);

  // ---------- add checkpoint ----------

  root.querySelectorAll('[data-testid="add-checkpoint"]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const weekId = parseInt(btn.dataset.weekId, 10);
      const list = root.querySelector(
        '[data-testid="checkpoint-list"][data-week-id="' + weekId + '"]'
      );
      if (!list) { return; }

      // Optimistic empty chip; replace with server-issued id on success.
      const li = document.createElement('li');
      li.className = 'plan-editor-checkpoint group flex items-center gap-2 px-3 py-2 rounded-lg bg-secondary border border-border text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-accent';
      li.setAttribute('data-testid', 'checkpoint-chip');
      li.setAttribute('data-checkpoint-id', '');
      li.setAttribute('data-week-id', String(weekId));
      li.setAttribute('data-done', 'false');
      li.setAttribute('tabindex', '0');
      li.innerHTML =
        '<span class="plan-editor-drag-handle cursor-grab text-muted-foreground" aria-hidden="true">::</span>' +
        '<input type="checkbox" data-testid="checkpoint-done-toggle" class="plan-editor-checkpoint-done">' +
        '<span class="plan-editor-checkpoint-text flex-1" data-testid="checkpoint-text"></span>' +
        '<button type="button" data-testid="checkpoint-delete" class="plan-editor-checkpoint-delete text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">x</button>';
      list.appendChild(li);

      apiCall('POST', 'weeks/' + weekId + '/checkpoints', {
        description: '',
      }).then(function (result) {
        if (result.ok && result.data && result.data.id) {
          li.dataset.checkpointId = String(result.data.id);
          li.dataset.position = String(result.data.position);
          bindCheckpointChip(li);
          // Auto-enter inline edit so the user can type immediately.
          const textEl = li.querySelector('[data-testid="checkpoint-text"]');
          enterInlineEdit(li, textEl, result.data.id);
          // Hide the empty-week hint if it was visible.
          const hint = btn.parentNode.querySelector('[data-testid="empty-week-hint"]');
          if (hint) { hint.classList.add('hidden'); }
        } else {
          li.remove();
          setIndicator('failed', result.message);
          showToast("Couldn't add checkpoint (" + result.code + ').');
        }
      });
    });
  });

  // ---------- interview notes (Internal/External tabs) ----------

  function renderNotes() {
    const internalUl = root.querySelector('[data-testid="interview-notes-internal"]');
    const externalUl = root.querySelector('[data-testid="interview-notes-external"]');
    if (!internalUl || !externalUl) { return; }
    const notes = (plan.interview_notes && plan.interview_notes) || { internal: [], external: [] };
    [['internal', internalUl], ['external', externalUl]].forEach(function (pair) {
      const visibility = pair[0];
      const ul = pair[1];
      ul.innerHTML = '';
      const items = notes[visibility] || [];
      if (items.length === 0) {
        const empty = document.createElement('li');
        empty.className = 'text-xs text-muted-foreground';
        empty.textContent = 'No ' + visibility + ' notes yet';
        empty.setAttribute('data-testid', 'interview-notes-empty');
        ul.appendChild(empty);
        return;
      }
      items.forEach(function (note) {
        const li = document.createElement('li');
        li.className = 'px-3 py-2 rounded-lg bg-secondary border border-border text-sm text-foreground';
        li.setAttribute('data-testid', 'interview-note-row');
        li.setAttribute('data-note-id', String(note.id));
        li.textContent = note.body;
        ul.appendChild(li);
      });
    });
  }

  const tabs = root.querySelectorAll('.plan-editor-notes-tab');
  tabs.forEach(function (btn) {
    btn.addEventListener('click', function () {
      const target = btn.dataset.tab;
      tabs.forEach(function (t) {
        const active = t.dataset.tab === target;
        t.setAttribute('aria-selected', active ? 'true' : 'false');
        if (active) {
          t.classList.add('border-accent', 'text-foreground');
          t.classList.remove('border-transparent', 'text-muted-foreground');
        } else {
          t.classList.remove('border-accent', 'text-foreground');
          t.classList.add('border-transparent', 'text-muted-foreground');
        }
      });
      root.querySelectorAll('[data-tab-panel]').forEach(function (panel) {
        panel.hidden = panel.dataset.tabPanel !== target;
      });
    });
  });

  renderNotes();

  // ---------- expose for E2E tests ----------

  // The E2E suite asserts on the editor's reconciliation surface; expose
  // the readers here so tests don't have to scrape DOM internals.
  window.__planEditor = {
    apiBase: apiBase,
    plan: plan,
    setIndicator: setIndicator,
    apiCall: apiCall,
  };
})();

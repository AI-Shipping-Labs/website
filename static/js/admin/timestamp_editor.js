(function () {
  function secondsToTimeStr(totalSeconds) {
    const safeSeconds = parseInt(totalSeconds, 10) || 0;
    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    const seconds = safeSeconds % 60;
    if (hours > 0) {
      return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    }
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }

  function timeStrToSeconds(timeStr) {
    if (!timeStr) return 0;
    const parts = timeStr.trim().split(':');
    if (parts.length === 2) {
      return (parseInt(parts[0], 10) || 0) * 60 + (parseInt(parts[1], 10) || 0);
    }
    if (parts.length === 3) {
      return (
        (parseInt(parts[0], 10) || 0) * 3600
        + (parseInt(parts[1], 10) || 0) * 60
        + (parseInt(parts[2], 10) || 0)
      );
    }
    return 0;
  }

  function buildRow(tbody, timeSeconds, label) {
    const tr = document.createElement('tr');

    const numberCell = document.createElement('td');
    numberCell.className = 'timestamp-editor__cell timestamp-editor__cell--number ts-row-number';
    numberCell.textContent = tbody.querySelectorAll('tr').length + 1;

    const timeCell = document.createElement('td');
    timeCell.className = 'timestamp-editor__cell';
    const timeInput = document.createElement('input');
    timeInput.type = 'text';
    timeInput.className = 'timestamp-editor__time-input ts-time-input';
    timeInput.value = secondsToTimeStr(timeSeconds || 0);
    timeInput.placeholder = '00:00';
    timeCell.appendChild(timeInput);

    const labelCell = document.createElement('td');
    labelCell.className = 'timestamp-editor__cell';
    const labelInput = document.createElement('input');
    labelInput.type = 'text';
    labelInput.className = 'timestamp-editor__label-input ts-label-input';
    labelInput.value = label || '';
    labelInput.placeholder = 'Timestamp label';
    labelCell.appendChild(labelInput);

    const actionCell = document.createElement('td');
    actionCell.className = 'timestamp-editor__cell timestamp-editor__actions';
    actionCell.innerHTML = (
      '<button type="button" class="timestamp-editor__row-button ts-move-up" title="Move up">&#9650;</button>'
      + '<button type="button" class="timestamp-editor__row-button ts-move-down" title="Move down">&#9660;</button>'
      + '<button type="button" class="timestamp-editor__row-button timestamp-editor__row-button--danger ts-remove" title="Remove">&#10005;</button>'
    );

    tr.append(numberCell, timeCell, labelCell, actionCell);
    return tr;
  }

  function initEditor(editor) {
    const hiddenInput = editor.querySelector('input[type="hidden"]');
    const tbody = editor.querySelector('[data-timestamp-rows]');
    const addBtn = editor.querySelector('[data-timestamp-add]');
    if (!hiddenInput || !tbody || !addBtn) return;

    function updateHiddenInput() {
      const rows = tbody.querySelectorAll('tr');
      const timestamps = [];
      rows.forEach((row) => {
        const timeInput = row.querySelector('.ts-time-input');
        const labelInput = row.querySelector('.ts-label-input');
        if (timeInput && labelInput) {
          timestamps.push({
            time_seconds: timeStrToSeconds(timeInput.value),
            label: labelInput.value,
          });
        }
      });
      hiddenInput.value = JSON.stringify(timestamps);
    }

    function renumberRows() {
      const rows = tbody.querySelectorAll('tr');
      rows.forEach((row, index) => {
        row.querySelector('.ts-row-number').textContent = index + 1;
      });
    }

    function addRow(timeSeconds, label) {
      const tr = buildRow(tbody, timeSeconds, label);
      tbody.appendChild(tr);

      tr.querySelector('.ts-time-input').addEventListener('change', updateHiddenInput);
      tr.querySelector('.ts-label-input').addEventListener('change', updateHiddenInput);
      tr.querySelector('.ts-label-input').addEventListener('input', updateHiddenInput);
      tr.querySelector('.ts-time-input').addEventListener('input', updateHiddenInput);

      tr.querySelector('.ts-remove').addEventListener('click', () => {
        tr.remove();
        renumberRows();
        updateHiddenInput();
      });

      tr.querySelector('.ts-move-up').addEventListener('click', () => {
        const prev = tr.previousElementSibling;
        if (prev) {
          tbody.insertBefore(tr, prev);
          renumberRows();
          updateHiddenInput();
        }
      });

      tr.querySelector('.ts-move-down').addEventListener('click', () => {
        const next = tr.nextElementSibling;
        if (next) {
          tbody.insertBefore(next, tr);
          renumberRows();
          updateHiddenInput();
        }
      });
    }

    try {
      const existing = JSON.parse(hiddenInput.value || '[]');
      if (Array.isArray(existing)) {
        existing.forEach((ts) => addRow(ts.time_seconds, ts.label));
      }
    } catch (error) {
      // Keep the admin usable even if stored JSON is malformed.
    }

    addBtn.addEventListener('click', () => {
      addRow(0, '');
      updateHiddenInput();
    });
  }

  function initAllEditors() {
    document.querySelectorAll('[data-timestamp-editor]').forEach(initEditor);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAllEditors);
  } else {
    initAllEditors();
  }
}());

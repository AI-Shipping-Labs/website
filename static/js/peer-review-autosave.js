(function () {
  var form = document.querySelector('[data-testid="peer-review-form"][data-autosave-url]');
  if (!form) return;
  var status = form.querySelector('[data-save-status]');
  var submitted = status && status.textContent.trim() !== '';
  var timer = null;
  var inflight = null;
  var dirty = false;
  function show(text) { if (status && !submitted) status.textContent = text; }
  function save() {
    clearTimeout(timer);
    if (!dirty || submitted) return;
    if (inflight) { timer = setTimeout(save, 300); return; }
    dirty = false;
    show('Saving...');
    var data = new FormData(form);
    data.set('action', 'draft');
    inflight = fetch(form.getAttribute('data-autosave-url'), {
      method: 'POST', body: data, credentials: 'same-origin',
      headers: {'X-Requested-With': 'XMLHttpRequest'},
    }).then(function (r) { return r.json().then(function (j) { return [r, j]; }); })
      .then(function (res) {
        if (res[0].ok && res[1].saved) show('Saved');
        else if (res[1].submitted) { submitted = true; }
        else show('Could not save. Use Submit to keep your answers.');
      })
      .catch(function () { show('Could not save. Use Submit to keep your answers.'); })
      .then(function () { inflight = null; });
  }
  function schedule(delay) { dirty = true; clearTimeout(timer); timer = setTimeout(save, delay); }
  form.addEventListener('change', function () { schedule(0); });
  form.addEventListener('input', function (e) {
    if (e.target.tagName === 'TEXTAREA') schedule(800);
  });
  form.addEventListener('focusout', function (e) {
    if (e.target.tagName === 'TEXTAREA') save();
  });
})();

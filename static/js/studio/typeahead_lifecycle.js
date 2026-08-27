(function (global) {
  'use strict';

  if (global.StudioTypeaheadLifecycle) {
    return;
  }

  function create(options) {
    var input = options.input;
    var list = options.list;
    var debounceMs = options.debounceMs || 200;
    var minQueryLength = options.minQueryLength || 2;
    var generation = 0;
    var debounce = null;
    var active = false;

    function clearList() {
      list.classList.add('hidden');
      list.innerHTML = '';
      if (options.onClear) {
        options.onClear();
      }
    }

    function invalidate() {
      generation += 1;
      active = false;
      if (debounce) {
        clearTimeout(debounce);
        debounce = null;
      }
      clearList();
    }

    function eligible(requestGeneration, query) {
      return active &&
        generation === requestGeneration &&
        input.value.trim() === query &&
        document.activeElement === input;
    }

    function searchCurrentValue() {
      if (options.onInput) {
        options.onInput();
      }

      // Every input event supersedes both pending debounce work and any
      // response that belongs to the previous value.
      invalidate();
      var query = input.value.trim();
      if (query.length < minQueryLength) {
        return;
      }

      active = true;
      var requestGeneration = generation;
      debounce = setTimeout(function () {
        debounce = null;
        options.fetchResults(query)
          .then(function (results) {
            if (!eligible(requestGeneration, query)) {
              return;
            }
            options.renderResults(results || []);
          })
          .catch(function () {
            if (eligible(requestGeneration, query)) {
              invalidate();
            }
          });
      }, debounceMs);
    }

    input.addEventListener('input', searchCurrentValue);
    input.addEventListener('blur', invalidate);

    return {
      dismiss: invalidate,
      select: invalidate,
      escape: invalidate,
    };
  }

  global.StudioTypeaheadLifecycle = {create: create};
})(window);

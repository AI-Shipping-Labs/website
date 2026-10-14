(function (global) {
  'use strict';

  var TERMINAL = {};

  function create(options) {
    var interval = options.interval;
    var maxBackoff = options.maxBackoff || interval * 16;
    var timer = null;
    var inFlight = false;
    var failures = 0;
    var nextDelay = interval;
    var started = false;
    var terminal = false;
    var active = false;

    function eligible() {
      return (
        started &&
        !terminal &&
        document.visibilityState === 'visible' &&
        (!options.shouldPoll || options.shouldPoll())
      );
    }

    function setActive(next) {
      if (active === next) return;
      active = next;
      if (options.onActiveChange) options.onActiveChange(next);
    }

    function clearTimer() {
      if (timer === null) return;
      global.clearTimeout(timer);
      timer = null;
    }

    function syncActive() {
      var next = eligible();
      setActive(next);
      if (!next) clearTimer();
      return next;
    }

    function schedule(delay) {
      clearTimer();
      if (!syncActive() || inFlight) return;
      timer = global.setTimeout(run, delay);
    }

    function stopTerminal() {
      terminal = true;
      clearTimer();
      setActive(false);
    }

    function completeSuccess(result) {
      inFlight = false;
      if (result === TERMINAL || terminal) return;
      failures = 0;
      nextDelay = interval;
      schedule(nextDelay);
    }

    function completeFailure(error) {
      inFlight = false;
      failures += 1;
      nextDelay = Math.min(maxBackoff, interval * Math.pow(2, failures));
      try {
        if (options.onError) options.onError(error, failures);
      } finally {
        schedule(nextDelay);
      }
    }

    function validateResponse(response) {
      var redirectedToLogin = Boolean(
        response &&
        response.redirected &&
        /\/accounts\/login(?:\/|\?|$)/.test(response.url)
      );
      if (
        response &&
        (response.status === 401 || response.status === 403 || redirectedToLogin)
      ) {
        stopTerminal();
        return TERMINAL;
      }
      if (response && response.ok === false) {
        var error = new Error('Polling request failed with status ' + response.status);
        error.status = response.status;
        throw error;
      }
      if (options.onResponse) return options.onResponse(response);
      return response;
    }

    function run() {
      timer = null;
      if (!syncActive() || inFlight) return;
      inFlight = true;
      Promise.resolve()
        .then(options.request)
        .then(validateResponse)
        .then(completeSuccess, completeFailure);
    }

    function start() {
      if (started || terminal) return;
      started = true;
      if (!syncActive()) return;
      if (options.immediate) {
        run();
      } else {
        schedule(nextDelay);
      }
    }

    function stop() {
      started = false;
      clearTimer();
      setActive(false);
    }

    document.addEventListener('visibilitychange', function () {
      if (!syncActive() || inFlight) return;
      schedule(nextDelay);
    });

    return {
      start: start,
      stop: stop,
    };
  }

  global.AISLVisibilityPoller = { create: create };
})(window);

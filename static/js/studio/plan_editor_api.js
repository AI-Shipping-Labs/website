/*
 * API/write helper for the Studio plan editor.
 *
 * This module intentionally knows nothing about the editor DOM. Callers
 * provide save-status callbacks and rollback functions.
 */
(function () {
  'use strict';

  function createClient(options) {
    const fetchImpl = options.fetchImpl || window.fetch.bind(window);
    const apiBase = options.apiBase || '/api/';
    const apiToken = options.apiToken || '';
    const retryDelayMs = options.retryDelayMs === undefined ? 1000 : options.retryDelayMs;
    const onSaving = options.onSaving || function () {};
    const onSaved = options.onSaved || function () {};
    const onFailed = options.onFailed || function () {};
    const onToast = options.onToast || function () {};
    let inflight = 0;

    function normalizeError(status, data, fallbackMessage) {
      return {
        ok: false,
        status: status,
        data: data || null,
        code: (data && data.code) || (status ? 'http_' + status : 'network_error'),
        message: (data && data.error) || fallbackMessage,
      };
    }

    function request(method, path, body) {
      inflight += 1;
      onSaving();

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

      return fetchImpl(url, init).then(function (resp) {
        const isJson = (resp.headers.get('content-type') || '').indexOf('application/json') !== -1;
        const hasBody = resp.status !== 204 && resp.status !== 205;
        const parse = (isJson && hasBody) ? resp.json() : Promise.resolve(null);
        return parse.then(function (data) {
          inflight -= 1;
          if (resp.ok) {
            if (inflight === 0) { onSaved(); }
            return { ok: true, status: resp.status, data: data };
          }
          return normalizeError(
            resp.status,
            data,
            'Request failed: ' + resp.status,
          );
        });
      }).catch(function (err) {
        inflight -= 1;
        return normalizeError(0, null, String(err));
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
              onToast(
                failureMessage
                || "Couldn't save change — your edit was reverted (" + retry.code + ').'
              );
              resolve(retry);
            });
          }, retryDelayMs);
        });
      });
    }

    return {
      request: request,
      writeWithRetry: writeWithRetry,
      getInflightCount: function () { return inflight; },
    };
  }

  window.PlanEditorApi = {
    createClient: createClient,
  };
})();

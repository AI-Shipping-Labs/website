// Issue #1795: localize course schedule instants to the browser timezone on
// a first visit (no valid saved preferred_timezone). Mirrors the event-page
// contract (templates/events/_event_time_display.html + event_detail.js):
// the server always renders a truthful explicit fallback zone, and this
// script rewrites the rendered labels and zone names in place — no reload,
// no persistence. The semantic <time datetime> attributes keep carrying the
// UTC instants, so screen readers and machine consumers are unaffected.
(function () {
  'use strict';

  function isValidTimeZone(name) {
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: name }).format(new Date());
      return true;
    } catch (error) {
      return false;
    }
  }

  function browserTimeZone() {
    try {
      return window.Intl.DateTimeFormat().resolvedOptions().timeZone || null;
    } catch (error) {
      return null;
    }
  }

  function intlParts(date, zone, options) {
    var formatter = new Intl.DateTimeFormat(
      'en-US',
      Object.assign({ timeZone: zone }, options)
    );
    return formatter.formatToParts(date).reduce(function (acc, part) {
      acc[part.type] = part.value;
      return acc;
    }, {});
  }

  // "Oct 6, 2026 01:59" — matches schedule_tags.schedule_datetime_local.
  function fullLabel(date, zone) {
    var p = intlParts(date, zone, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    });
    return p.month + ' ' + p.day + ', ' + p.year + ' ' + p.hour + ':' + p.minute;
  }

  // "Mon Oct 6, 01:59" — matches course_commitments._short_when_label.
  function shortLabel(date, zone) {
    var p = intlParts(date, zone, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    });
    return p.weekday + ' ' + p.month + ' ' + p.day + ', ' + p.hour + ':' + p.minute;
  }

  // "October 6, 2026, 01:59" — matches format_event_time_range without the
  // zone suffix (course_commitments.when_label_without_timezone).
  function longLabel(date, zone) {
    var p = intlParts(date, zone, {
      month: 'long',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    });
    return p.month + ' ' + p.day + ', ' + p.year + ', ' + p.hour + ':' + p.minute;
  }

  // "October 6, 2026, 01:59 America/New_York" — matches
  // format_event_time_range for a single instant (homework reader due line).
  function zoneLabel(date, zone) {
    return longLabel(date, zone) + ' ' + zone;
  }

  // data-schedule-format -> formatter; a missing value means fullLabel.
  var FORMATTERS = {
    short: shortLabel,
    long: longLabel,
    zone: zoneLabel,
  };

  function localize(root) {
    var zone = browserTimeZone();
    if (!zone || !isValidTimeZone(zone)) return;
    if (zone === root.dataset.serverTimezone) return;
    root.querySelectorAll('[data-schedule-instant]').forEach(function (node) {
      var instant = node.getAttribute('datetime');
      if (!instant) return;
      var date = new Date(instant);
      if (isNaN(date.getTime())) return;
      var format = FORMATTERS[node.getAttribute('data-schedule-format')] || fullLabel;
      node.textContent = format(date, zone);
    });
    root.querySelectorAll('[data-schedule-zone-label]').forEach(function (node) {
      node.textContent = zone;
    });
  }

  if (!window.Intl || typeof window.Intl.DateTimeFormat !== 'function') return;
  document
    .querySelectorAll('[data-course-schedule][data-localize="true"]')
    .forEach(localize);
})();

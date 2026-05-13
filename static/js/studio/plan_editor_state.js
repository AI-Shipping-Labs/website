/*
 * Pure checkpoint state transforms for the Studio plan editor.
 *
 * Inputs and outputs are plain arrays of:
 *   { id: number, weekId: number, position: number }
 */
(function () {
  'use strict';

  function normalizeSnapshot(snapshot) {
    return snapshot.slice().sort(function (a, b) {
      if (a.weekId !== b.weekId) { return a.weekId - b.weekId; }
      if (a.position !== b.position) { return a.position - b.position; }
      return a.id - b.id;
    }).map(function (entry) {
      return {
        id: parseInt(entry.id, 10),
        weekId: parseInt(entry.weekId, 10),
        position: parseInt(entry.position, 10),
      };
    });
  }

  function weeksFromSnapshot(snapshot, weekIds) {
    const weeks = (weekIds || []).map(function (weekId) {
      return {
        weekId: parseInt(weekId, 10),
        checkpointIds: [],
      };
    });
    normalizeSnapshot(snapshot).forEach(function (entry) {
      let week = weeks.find(function (candidate) {
        return candidate.weekId === entry.weekId;
      });
      if (!week) {
        week = { weekId: entry.weekId, checkpointIds: [] };
        weeks.push(week);
      }
      week.checkpointIds.push(entry.id);
    });
    return weeks;
  }

  function snapshotFromWeeks(weeks) {
    const snapshot = [];
    weeks.forEach(function (week) {
      week.checkpointIds.forEach(function (id, idx) {
        snapshot.push({
          id: parseInt(id, 10),
          weekId: parseInt(week.weekId, 10),
          position: idx,
        });
      });
    });
    return snapshot;
  }

  function moveCheckpoint(snapshot, checkpointId, destWeekId, destPosition) {
    const id = parseInt(checkpointId, 10);
    const targetWeekId = parseInt(destWeekId, 10);
    const weeks = weeksFromSnapshot(snapshot);
    const movingEntry = normalizeSnapshot(snapshot).find(function (entry) {
      return entry.id === id;
    });
    if (!movingEntry) { return normalizeSnapshot(snapshot); }

    let targetWeek = weeks.find(function (week) {
      return week.weekId === targetWeekId;
    });
    if (!targetWeek) {
      targetWeek = { weekId: targetWeekId, checkpointIds: [] };
      weeks.push(targetWeek);
      weeks.sort(function (a, b) { return a.weekId - b.weekId; });
    }

    weeks.forEach(function (week) {
      week.checkpointIds = week.checkpointIds.filter(function (candidateId) {
        return candidateId !== id;
      });
    });

    const clampedPosition = Math.max(
      0,
      Math.min(parseInt(destPosition, 10), targetWeek.checkpointIds.length),
    );
    targetWeek.checkpointIds.splice(clampedPosition, 0, id);
    return snapshotFromWeeks(weeks);
  }

  function keyboardMoveTarget(snapshot, checkpointId, direction, crossWeek, weekIds) {
    const id = parseInt(checkpointId, 10);
    const normalized = normalizeSnapshot(snapshot);
    const weeks = weeksFromSnapshot(normalized, weekIds);
    const current = normalized.find(function (entry) { return entry.id === id; });
    if (!current) { return null; }

    if (!crossWeek) {
      const siblings = normalized.filter(function (entry) {
        return entry.weekId === current.weekId;
      });
      const siblingIndex = siblings.findIndex(function (entry) { return entry.id === id; });
      const position = siblingIndex + direction;
      if (position < 0 || position >= siblings.length) { return null; }
      return { weekId: current.weekId, position: position };
    }

    const weekIndex = weeks.findIndex(function (week) {
      return week.weekId === current.weekId;
    });
    const targetWeek = weeks[weekIndex + direction];
    if (!targetWeek) { return null; }
    return {
      weekId: targetWeek.weekId,
      position: direction > 0 ? 0 : targetWeek.checkpointIds.length,
    };
  }

  window.PlanEditorState = {
    keyboardMoveTarget: keyboardMoveTarget,
    moveCheckpoint: moveCheckpoint,
    normalizeSnapshot: normalizeSnapshot,
    snapshotFromWeeks: snapshotFromWeeks,
    weeksFromSnapshot: weeksFromSnapshot,
  };
})();

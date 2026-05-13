/* Workshop course-player layout JS module (issue #618).
 *
 * Loaded ONLY for users who can access the recording (the Django
 * template gates the <script> tag). Locked users get zero player JS,
 * by design — chapter rows, section badges, and tutorial-page rows
 * remain plain non-interactive markup for them.
 *
 * Responsibilities:
 * - Lazy-mount the YouTube/Loom iframe on the first interaction
 *   (chapter click, section badge, tutorial page click, or the play
 *   overlay tap). Saves a third-party request for read-only visits.
 * - Seek the player on chapter/badge/page click; for unmounted state
 *   the start position is captured and used to mount with `start=N`.
 * - Swap the right-pane tutorial body via a `?_partial=1` fetch
 *   (no full page reload). Updates the URL via history.pushState.
 * - Persist the last play position in localStorage so a refresh resumes
 *   roughly where the user was. Keyed by workshop slug.
 * - Highlight the active chapter row + active tutorial-page row as the
 *   player advances.
 */
(function () {
  'use strict';

  var shell = document.getElementById('workshop-player-shell');
  if (!shell) {
    return;
  }

  var workshopSlug = (window.location.pathname.match(
    /^\/workshops\/([^\/]+)/,
  ) || [])[1];
  if (!workshopSlug) {
    return;
  }

  var sourceType = shell.getAttribute('data-source');
  var videoId = shell.getAttribute('data-video-id');
  var embedUrl = shell.getAttribute('data-embed-url');
  var initialStart = parseInt(
    shell.getAttribute('data-start-seconds') || '0', 10,
  ) || 0;

  // localStorage resume — read once on load, write throttled on time
  // advance. Cap at 6 hours so a stale value can't seek to an absurd
  // position.
  var resumeKey = 'workshop-player-resume:' + workshopSlug;
  var initialResume = 0;
  try {
    var raw = window.localStorage.getItem(resumeKey);
    var parsed = raw ? parseInt(raw, 10) : 0;
    if (parsed > 0 && parsed < 6 * 3600) {
      initialResume = parsed;
    }
  } catch (e) {
    // Private browsing / no storage — silently no-op.
  }
  // ?t= deep link wins over localStorage resume.
  var pendingSeek = initialStart || initialResume || 0;

  var ytPlayer = null;
  var loomIframe = null;
  var iframeMounted = false;
  var lastSavedSeconds = 0;
  var saveTimer = null;

  // Public hook so the inline timestamp tags from `_video_chapters_body`
  // (used elsewhere on the site) can still seek a YT player when the
  // course-player layout owns the page.
  window._ytPlayers = window._ytPlayers || {};

  function mountIframe(seekSeconds) {
    if (iframeMounted) {
      return;
    }
    iframeMounted = true;

    // Remove the play overlay button so it doesn't sit on top of the
    // freshly-mounted iframe.
    var overlay = document.getElementById('workshop-player-play-overlay');
    if (overlay && overlay.parentNode) {
      overlay.parentNode.removeChild(overlay);
    }

    if (sourceType === 'youtube' && videoId) {
      // Inject a div the IFrame API can replace.
      var holder = document.createElement('div');
      holder.id = 'workshop-yt-player-' + videoId;
      holder.className = 'h-full w-full';
      shell.appendChild(holder);

      var startVar = seekSeconds || 0;

      function initYT() {
        if (!window.YT || !window.YT.Player) {
          return false;
        }
        ytPlayer = new window.YT.Player(holder.id, {
          videoId: videoId,
          playerVars: {
            enablejsapi: 1,
            modestbranding: 1,
            rel: 0,
            autoplay: 1,
            start: startVar,
          },
          events: {
            onReady: function () {
              // Save the player so other timestamp buttons can find it.
              window._ytPlayers[videoId] = ytPlayer;
              startTimePoll();
            },
          },
        });
        return true;
      }

      if (window.YT && window.YT.Player) {
        initYT();
      } else {
        // Load the IFrame API script if it isn't already loaded.
        if (
          !document.querySelector(
            'script[src*="youtube.com/iframe_api"]',
          )
        ) {
          var tag = document.createElement('script');
          tag.src = 'https://www.youtube.com/iframe_api';
          var firstScript = document.getElementsByTagName('script')[0];
          firstScript.parentNode.insertBefore(tag, firstScript);
        }
        var prev = window.onYouTubeIframeAPIReady;
        window.onYouTubeIframeAPIReady = function () {
          if (prev) {
            prev();
          }
          initYT();
        };
      }
    } else if (sourceType === 'loom' && videoId) {
      var loomBase = 'https://www.loom.com/embed/' + videoId;
      var loomSrc = embedUrl || loomBase;
      if (seekSeconds) {
        var sep = loomSrc.indexOf('?') >= 0 ? '&' : '?';
        loomSrc = loomSrc + sep + 't=' + seekSeconds;
      }
      loomIframe = document.createElement('iframe');
      loomIframe.id = 'workshop-loom-player-' + videoId;
      loomIframe.src = loomSrc;
      loomIframe.className = 'h-full w-full';
      loomIframe.setAttribute('allowfullscreen', '');
      shell.appendChild(loomIframe);
    }
  }

  function seekTo(seconds) {
    if (!iframeMounted) {
      mountIframe(seconds);
      return;
    }
    if (ytPlayer && typeof ytPlayer.seekTo === 'function') {
      ytPlayer.seekTo(seconds, true);
      if (typeof ytPlayer.playVideo === 'function') {
        ytPlayer.playVideo();
      }
      return;
    }
    if (loomIframe) {
      var base = 'https://www.loom.com/embed/' + videoId;
      loomIframe.src = base + '?t=' + seconds;
    }
  }

  function startTimePoll() {
    if (!ytPlayer) {
      return;
    }
    setInterval(function () {
      var t = 0;
      try {
        if (typeof ytPlayer.getCurrentTime === 'function') {
          t = Math.floor(ytPlayer.getCurrentTime() || 0);
        }
      } catch (e) {
        return;
      }
      if (t > 0 && Math.abs(t - lastSavedSeconds) >= 5) {
        lastSavedSeconds = t;
        try {
          window.localStorage.setItem(resumeKey, String(t));
        } catch (e) {
          // ignore
        }
        highlightActiveChapter(t);
      }
    }, 2000);
  }

  function highlightActiveChapter(seconds) {
    var rows = document.querySelectorAll('.workshop-chapter-row');
    var activeRow = null;
    rows.forEach(function (row) {
      var rowSeconds = parseInt(
        row.getAttribute('data-time-seconds') || '0', 10,
      );
      if (rowSeconds <= seconds) {
        activeRow = row;
      }
    });
    rows.forEach(function (row) {
      if (row === activeRow) {
        row.classList.add('bg-accent/10', 'text-accent');
      } else {
        row.classList.remove('bg-accent/10', 'text-accent');
      }
    });
  }

  function setActiveTutorialPage(slug) {
    var rows = document.querySelectorAll('.workshop-outline-page-row');
    rows.forEach(function (row) {
      var href = row.getAttribute('href') || '';
      var matches = href.indexOf('/tutorial/' + slug) >= 0;
      if (matches) {
        row.setAttribute('aria-current', 'page');
        row.classList.add('bg-accent/10', 'text-accent', 'font-medium');
      } else {
        row.removeAttribute('aria-current');
        row.classList.remove('bg-accent/10', 'text-accent', 'font-medium');
      }
    });
  }

  function swapTutorialPane(slug, opts) {
    opts = opts || {};
    var url = '/workshops/' + workshopSlug + '?page=' + encodeURIComponent(slug)
      + '&_partial=1';
    fetch(url, { headers: { Accept: 'text/html' } })
      .then(function (res) {
        if (!res.ok) {
          return null;
        }
        return res.text();
      })
      .then(function (html) {
        if (!html) {
          return;
        }
        var existing = document.getElementById('workshop-tutorial-pane');
        if (!existing) {
          return;
        }
        // Parse the partial response and replace the tutorial pane.
        var parser = new DOMParser();
        var doc = parser.parseFromString(html, 'text/html');
        var fresh = doc.getElementById('workshop-tutorial-pane');
        if (fresh) {
          existing.parentNode.replaceChild(fresh, existing);
        }
        // Re-bind handlers on the fresh badges.
        bindSectionBadges();
        // Re-render Lucide icons inside the swapped pane.
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
          window.lucide.createIcons();
        }
        // Update the URL without a reload.
        var historyUrl = '/workshops/' + workshopSlug
          + '?page=' + encodeURIComponent(slug);
        if (window.history && window.history.pushState) {
          window.history.pushState({ slug: slug }, '', historyUrl);
        }
        setActiveTutorialPage(slug);
      })
      .catch(function () {
        // On fetch failure fall back to a normal navigation.
        window.location.href = '/workshops/' + workshopSlug
          + '?page=' + encodeURIComponent(slug);
      });
  }

  function bindChapterRows() {
    document.querySelectorAll('.workshop-chapter-row').forEach(function (row) {
      if (row._wsBound) {
        return;
      }
      row._wsBound = true;
      row.addEventListener('click', function () {
        var seconds = parseInt(
          row.getAttribute('data-time-seconds') || '0', 10,
        );
        seekTo(seconds);
        var slug = row.getAttribute('data-tutorial-slug');
        if (slug) {
          swapTutorialPane(slug);
        }
      });
    });
  }

  function bindOutlinePageRows() {
    document.querySelectorAll('.workshop-outline-page-row').forEach(
      function (anchor) {
        if (anchor._wsBound) {
          return;
        }
        anchor._wsBound = true;
        anchor.addEventListener('click', function (ev) {
          // The href is the standalone tutorial URL — for the player
          // shell we intercept and swap the right pane in-place so the
          // user doesn't lose the player. Hold cmd/ctrl/shift/alt to
          // preserve "open in new tab" behaviour.
          if (
            ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey
            || ev.button !== 0
          ) {
            return;
          }
          ev.preventDefault();
          var href = anchor.getAttribute('href') || '';
          var match = href.match(/\/tutorial\/([^?\/#]+)/);
          if (!match) {
            return;
          }
          var slug = match[1];
          swapTutorialPane(slug);
          // Look up the page's video_start from the chapter row map
          // and seek the player too. The chapter row carries
          // `data-tutorial-slug` for the page that anchors that
          // chapter; pick the lowest matching seconds.
          var chapter = null;
          document
            .querySelectorAll('.workshop-chapter-row')
            .forEach(function (row) {
              if (row.getAttribute('data-tutorial-slug') === slug) {
                if (chapter === null) {
                  chapter = parseInt(
                    row.getAttribute('data-time-seconds') || '0', 10,
                  );
                }
              }
            });
          if (chapter !== null) {
            seekTo(chapter);
          }
        });
      },
    );
  }

  function bindSectionBadges() {
    document.querySelectorAll('.workshop-section-badge').forEach(
      function (btn) {
        if (btn._wsBound) {
          return;
        }
        btn._wsBound = true;
        btn.addEventListener('click', function (ev) {
          ev.preventDefault();
          var seconds = parseInt(
            btn.getAttribute('data-time-seconds') || '0', 10,
          );
          seekTo(seconds);
        });
      },
    );
  }

  // Mount the iframe on first interaction with the play overlay.
  var overlay = document.getElementById('workshop-player-play-overlay');
  if (overlay) {
    overlay.addEventListener('click', function () {
      mountIframe(pendingSeek || 0);
    });
  }

  bindChapterRows();
  bindOutlinePageRows();
  bindSectionBadges();
})();

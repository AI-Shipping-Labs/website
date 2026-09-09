/*
 * Shared client-side Markdown fallback for sprint plan edits.
 *
 * Server-rendered plan content and checkpoint API HTML remain owned by
 * plans.templatetags.plan_markdown. This small renderer is used only when a
 * successful client update has no server-rendered HTML field.
 */
(function () {
  'use strict';

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

  window.SprintPlanMarkdown = Object.freeze({
    escapeHtml: escapeHtml,
    renderInline: renderInline,
    renderMarkdown: renderMarkdown,
  });
})();

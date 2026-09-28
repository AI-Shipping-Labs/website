(function () {
  "use strict";

  const MAX_SUGGESTIONS = 6;
  const MAX_EXCERPT_LENGTH = 220;

  function normalize(value) {
    return (value || "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase()
      .replace(/[^\p{L}\p{N}+#._-]+/gu, " ")
      .trim();
  }

  function cleanText(value) {
    const text = value && value.nodeType ? value.textContent : value;
    return (text || "").replace(/\s+/g, " ").trim();
  }

  function searchableText(element, options) {
    if (!element) return "";
    const clone = element.cloneNode(true);
    clone
      .querySelectorAll(
        '[aria-hidden="true"], .sr-only, [data-cb-syllabus-search-ignore]'
      )
      .forEach((node) => node.remove());
    if (options && options.titleOnly) {
      clone
        .querySelectorAll(
          '[data-search-excerpt], [data-testid*="deadline"], [class*="text-xs"]'
        )
        .forEach((node) => node.remove());
    }
    return cleanText(clone);
  }

  function truncate(value, maximum) {
    if (value.length <= maximum) return value;
    const shortened = value.slice(0, maximum + 1);
    const lastSpace = shortened.lastIndexOf(" ");
    return `${shortened.slice(0, lastSpace > maximum * 0.65 ? lastSpace : maximum).trim()}…`;
  }

  function initialize(root) {
    const input = root.querySelector("[data-cb-syllabus-search-input]");
    const suggestions = root.querySelector(
      "[data-cb-syllabus-search-suggestions]"
    );
    const status = root.nextElementSibling &&
      root.nextElementSibling.matches("[data-cb-syllabus-search-status]")
      ? root.nextElementSibling
      : null;
    const results = status && status.nextElementSibling &&
      status.nextElementSibling.matches("[data-cb-syllabus-search-results]")
      ? status.nextElementSibling
      : null;
    const resultsHeading = results &&
      results.querySelector("[data-cb-syllabus-search-results-heading]");
    const resultsList = results &&
      results.querySelector("[data-cb-syllabus-search-results-list]");
    const scope = root.closest("[data-cb-syllabus]") || root.parentElement;
    const submitUrl = root.dataset.cbSyllabusSearchSubmitUrl || "";
    const noResultsText = root.dataset.cbSyllabusSearchNoResultsText ||
      "No syllabus items match this search. Try fewer or broader words.";
    const noMatchesStatus = root.dataset.cbSyllabusSearchNoMatchesStatus ||
      "No matching syllabus items";

    if (
      !input ||
      !suggestions ||
      !status ||
      !results ||
      !resultsHeading ||
      !resultsList ||
      !scope
    ) {
      return;
    }

    const optionIdPrefix = `${input.id || "syllabus-search"}-suggestion`;
    const itemElements = Array.from(
      scope.querySelectorAll("[data-cb-syllabus-search-item]")
    );
    let activeSuggestion = -1;
    let blurTimer = null;

    function sectionHeading(section) {
      const explicit = section.querySelector(
        ":scope > [data-cb-syllabus-search-heading], " +
          ":scope > summary [data-cb-syllabus-search-heading]"
      );
      if (explicit) return searchableText(explicit, { titleOnly: true });

      const heading = section.querySelector(
        ":scope > h1, :scope > h2, :scope > h3, :scope > h4, " +
          ":scope > h5, :scope > h6, :scope > summary"
      );
      return searchableText(heading, { titleOnly: true });
    }

    function itemContext(item) {
      if (item.dataset.searchContext) {
        return [cleanText(item.dataset.searchContext)];
      }
      const parts = [];
      let section = item.closest("[data-cb-syllabus-search-section]");
      while (section && scope.contains(section)) {
        const heading = sectionHeading(section);
        if (heading && !parts.includes(heading)) parts.unshift(heading);
        section = section.parentElement
          ? section.parentElement.closest("[data-cb-syllabus-search-section]")
          : null;
      }
      return parts;
    }

    function itemTitle(item) {
      if (item.dataset.searchTitle) return cleanText(item.dataset.searchTitle);
      const titleElement = item.querySelector(
        "[data-cb-syllabus-search-title], .syllabus-title, " +
          '[class~="font-medium"], h1, h2, h3, h4, h5, h6'
      );
      return (
        searchableText(titleElement || item, { titleOnly: true }) ||
        "Syllabus item"
      );
    }

    function contextualUrl(rawUrl, item, index) {
      const current = new URL(window.location.href);
      let url;

      if (rawUrl) {
        url = new URL(rawUrl, current);
      } else {
        if (!item.id) {
          item.id = `${input.id || "syllabus-search"}-item-${index + 1}`;
        }
        url = new URL(current);
        url.hash = item.id;
      }

      if (url.origin === current.origin) {
        url.searchParams.delete("q");
        const cohort = current.searchParams.get("cohort");
        if (cohort && !url.searchParams.has("cohort")) {
          url.searchParams.set("cohort", cohort);
        }
      }
      return url.toString();
    }

    const records = itemElements.map((item, index) => {
      const title = itemTitle(item);
      const contextParts = itemContext(item);
      const body = searchableText(item);
      let excerpt = cleanText(item.dataset.searchExcerpt || body);
      if (excerpt.toLocaleLowerCase().startsWith(title.toLocaleLowerCase())) {
        excerpt = excerpt.slice(title.length).replace(/^\s*[-–—:·]?\s*/, "");
      }
      if (!excerpt) excerpt = "Open this syllabus item.";

      const link = item.matches("a[href]")
        ? item
        : item.querySelector("a[href]");
      const explicitUrl = item.dataset.searchUrl || (
        link ? link.getAttribute("href") : ""
      );

      return {
        index,
        item,
        title,
        titleNormalized: normalize(title),
        context: contextParts.join(" › "),
        contextNormalized: normalize(contextParts.join(" ")),
        excerpt: truncate(excerpt, MAX_EXCERPT_LENGTH),
        textNormalized: normalize(
          item.dataset.searchText ||
            [contextParts.join(" "), title, body].join(" ")
        ),
        url: contextualUrl(explicitUrl, item, index),
        usesFallbackUrl: !explicitUrl,
      };
    });

    function scoreRecord(record, query, terms) {
      if (!terms.every((term) => record.textNormalized.includes(term))) {
        return -1;
      }

      let score = 0;
      if (record.titleNormalized === query) score += 1000;
      if (record.titleNormalized.startsWith(query)) score += 450;
      else if (record.titleNormalized.includes(query)) score += 275;
      if (record.textNormalized.includes(query)) score += 150;

      const titleWords = record.titleNormalized.split(/\s+/);
      terms.forEach((term) => {
        if (titleWords.some((word) => word.startsWith(term))) score += 55;
        else if (record.titleNormalized.includes(term)) score += 35;
        else if (record.contextNormalized.includes(term)) score += 15;
        else score += 5;
      });
      return score;
    }

    function matchesFor(rawQuery) {
      const query = normalize(rawQuery);
      if (!query) return [];
      const terms = query.split(/\s+/).filter(Boolean);

      return records
        .map((record) => ({
          record,
          score: scoreRecord(record, query, terms),
        }))
        .filter((match) => match.score >= 0)
        .sort((left, right) => {
          return right.score - left.score ||
            left.record.index - right.record.index;
        })
        .map((match) => match.record);
    }

    function closeSuggestions() {
      suggestions.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      activeSuggestion = -1;
    }

    function activateSuggestion(index) {
      const options = Array.from(
        suggestions.querySelectorAll('[role="option"]:not([aria-disabled="true"])')
      );
      if (!options.length) return;
      activeSuggestion = (index + options.length) % options.length;
      options.forEach((option, optionIndex) => {
        option.setAttribute(
          "aria-selected",
          optionIndex === activeSuggestion ? "true" : "false"
        );
      });
      const active = options[activeSuggestion];
      input.setAttribute("aria-activedescendant", active.id);
      active.scrollIntoView({ block: "nearest" });
    }

    function restoreSyllabusForLink(record) {
      if (!record.usesFallbackUrl) return;
      scope.removeAttribute("data-cb-syllabus-search-results-active");
      results.hidden = true;
    }

    function createResultLink(record, className) {
      const link = document.createElement("a");
      link.href = record.url;
      link.className = className;
      if (record.usesFallbackUrl) {
        link.addEventListener("click", () => restoreSyllabusForLink(record));
      }
      return link;
    }

    function renderSuggestions(rawQuery) {
      const query = normalize(rawQuery);
      if (!query) {
        closeSuggestions();
        return;
      }

      const matches = matchesFor(rawQuery);
      suggestions.replaceChildren();
      activeSuggestion = -1;

      if (!matches.length) {
        const empty = document.createElement("li");
        empty.className = "cb-syllabus-search__empty";
        empty.setAttribute("role", "option");
        empty.setAttribute("aria-disabled", "true");
        empty.textContent = `No matches for “${cleanText(rawQuery)}”`;
        suggestions.append(empty);
      } else {
        matches.slice(0, MAX_SUGGESTIONS).forEach((record, index) => {
          const option = document.createElement("li");
          option.id = `${optionIdPrefix}-${index + 1}`;
          option.setAttribute("role", "option");
          option.setAttribute("aria-selected", "false");

          const link = createResultLink(
            record,
            "cb-syllabus-search__suggestion-link"
          );
          const title = document.createElement("span");
          title.className = "cb-syllabus-search__suggestion-title";
          title.textContent = record.title;
          link.append(title);

          if (record.context) {
            const context = document.createElement("span");
            context.className = "cb-syllabus-search__suggestion-context";
            context.textContent = record.context;
            link.append(context);
          }

          option.append(link);
          suggestions.append(option);
        });
      }

      suggestions.hidden = false;
      input.setAttribute("aria-expanded", "true");
      status.textContent = matches.length
        ? `${matches.length} ${matches.length === 1 ? "match" : "matches"} available`
        : noMatchesStatus;
    }

    function updateUrl(rawQuery, method) {
      const url = new URL(window.location.href);
      const query = cleanText(rawQuery);
      if (query) url.searchParams.set("q", query);
      else url.searchParams.delete("q");

      if (url.toString() !== window.location.href) {
        window.history[method](null, "", url);
      }
    }

    function showBrowse(updateHistory) {
      closeSuggestions();
      scope.removeAttribute("data-cb-syllabus-search-results-active");
      results.hidden = true;
      resultsHeading.textContent = "";
      resultsList.replaceChildren();
      status.textContent = "";
      if (updateHistory) updateUrl("", "replaceState");
    }

    function renderFullResults(rawQuery, updateHistory, moveFocus) {
      const query = cleanText(rawQuery);
      if (!normalize(query)) {
        showBrowse(updateHistory);
        return;
      }

      const matches = matchesFor(query);
      closeSuggestions();
      scope.setAttribute("data-cb-syllabus-search-results-active", "");
      results.hidden = false;
      resultsHeading.textContent = `Search results for “${query}”`;
      resultsList.replaceChildren();

      if (!matches.length) {
        const empty = document.createElement("p");
        empty.className = "cb-syllabus-search__empty";
        empty.textContent = noResultsText;
        resultsList.append(empty);
      } else {
        const list = document.createElement("ol");
        list.className = "cb-syllabus-search__result-list";

        matches.forEach((record) => {
          const listItem = document.createElement("li");
          listItem.className = "cb-syllabus-search__result";
          const article = document.createElement("article");

          if (record.context) {
            const context = document.createElement("div");
            context.className = "cb-syllabus-search__result-context";
            context.textContent = record.context;
            article.append(context);
          }

          const title = document.createElement("h4");
          title.className = "cb-syllabus-search__result-title";
          const link = createResultLink(record, "");
          link.textContent = record.title;
          title.append(link);
          article.append(title);

          const excerpt = document.createElement("p");
          excerpt.className = "cb-syllabus-search__result-excerpt";
          excerpt.textContent = record.excerpt;
          article.append(excerpt);

          listItem.append(article);
          list.append(listItem);
        });

        resultsList.append(list);
      }

      status.textContent =
        `${matches.length} ${matches.length === 1 ? "result" : "results"}`;
      if (updateHistory) updateUrl(query, "pushState");
      if (moveFocus) {
        results.setAttribute("tabindex", "-1");
        results.focus({ preventScroll: true });
        results.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }

    root.addEventListener("submit", (event) => {
      event.preventDefault();
      if (submitUrl) {
        const url = new URL(submitUrl, window.location.href);
        const currentUrl = new URL(window.location.href);
        for (const [key, value] of currentUrl.searchParams) {
          if (key !== "q" && !url.searchParams.has(key)) {
            url.searchParams.set(key, value);
          }
        }
        const query = cleanText(input.value);
        if (query) url.searchParams.set("q", query);
        else url.searchParams.delete("q");
        window.location.assign(url.toString());
        return;
      }
      renderFullResults(input.value, true, true);
    });

    input.addEventListener("input", () => {
      if (!normalize(input.value) && !results.hidden) {
        showBrowse(true);
        return;
      }
      renderSuggestions(input.value);
    });

    input.addEventListener("focus", () => {
      if (normalize(input.value)) renderSuggestions(input.value);
    });

    input.addEventListener("blur", () => {
      blurTimer = window.setTimeout(closeSuggestions, 150);
    });

    suggestions.addEventListener("pointerdown", () => {
      if (blurTimer !== null) window.clearTimeout(blurTimer);
    });

    input.addEventListener("keydown", (event) => {
      const enabledOptions = suggestions.querySelectorAll(
        '[role="option"]:not([aria-disabled="true"])'
      );

      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (suggestions.hidden) renderSuggestions(input.value);
        activateSuggestion(activeSuggestion + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        if (suggestions.hidden) renderSuggestions(input.value);
        activateSuggestion(activeSuggestion - 1);
      } else if (event.key === "Escape") {
        closeSuggestions();
      } else if (
        event.key === "Enter" &&
        !suggestions.hidden &&
        activeSuggestion >= 0
      ) {
        const active = enabledOptions[activeSuggestion];
        const activeLink = active && active.querySelector("a[href]");
        if (activeLink) {
          event.preventDefault();
          activeLink.click();
        }
      }
    });

    document.addEventListener("pointerdown", (event) => {
      if (!root.contains(event.target)) closeSuggestions();
    });

    window.addEventListener("popstate", () => {
      const query = new URL(window.location.href).searchParams.get("q") || "";
      input.value = query;
      if (normalize(query)) renderFullResults(query, false, false);
      else showBrowse(false);
    });

    const initialQuery =
      new URL(window.location.href).searchParams.get("q") || "";
    input.value = initialQuery;
    if (normalize(initialQuery)) {
      renderFullResults(initialQuery, false, false);
    } else {
      showBrowse(false);
    }
  }

  document.querySelectorAll("[data-cb-syllabus-search]").forEach(initialize);
})();

document.querySelectorAll("[data-learning-public-links]").forEach((group) => {
  const maxLinks = Number.parseInt(group.dataset.maxLinks, 10);
  const valueField = group.querySelector("[data-public-links-value]");
  const slots = group.querySelector("[data-public-link-slots]");
  const addButton = group.querySelector("[data-add-public-link]");
  if (!valueField || !slots || !addButton || !Number.isFinite(maxLinks) || maxLinks < 1) {
    group.hidden = true;
    return;
  }

  const savedLinks = valueField.value.split(/\r?\n/).map((link) => link.trim()).filter(Boolean);

  const updateAnswer = () => {
    valueField.value = Array.from(slots.querySelectorAll("[data-public-link-input]"))
      .map((input) => input.value.trim())
      .filter(Boolean)
      .join("\n");
  };

  const renderSlots = (values, focusIndex = -1) => {
    slots.replaceChildren();
    values.forEach((value, index) => {
      const row = document.createElement("div");
      row.className = "flex items-start gap-2";

      const field = document.createElement("div");
      field.className = "min-w-0 flex-1";

      const label = document.createElement("label");
      label.className = "mb-2 block text-sm text-muted-foreground";
      label.htmlFor = `learning-public-link-${index + 1}`;
      label.textContent = `Public link ${index + 1}`;

      const input = document.createElement("input");
      input.id = label.htmlFor;
      input.type = "url";
      input.inputMode = "url";
      input.autocomplete = "url";
      input.placeholder = "https://";
      input.value = value;
      input.dataset.publicLinkInput = "true";
      input.className = "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "mt-8 shrink-0 rounded-md border border-border px-3 py-2 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
      remove.dataset.removePublicLink = "true";
      remove.setAttribute("aria-label", `Remove link ${index + 1}`);
      remove.textContent = "Remove";

      field.append(label, input);
      row.append(field, remove);
      slots.append(row);
    });

    addButton.hidden = values.length >= maxLinks;
    addButton.textContent = values.length ? "Add another link" : "Add a link";
    if (focusIndex >= 0) slots.querySelectorAll("[data-public-link-input]")[focusIndex]?.focus();
  };

  slots.addEventListener("input", (event) => {
    if (event.target.matches("[data-public-link-input]")) updateAnswer();
  });
  slots.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove-public-link]");
    if (!remove) return;
    const row = remove.closest("div.flex");
    const rows = Array.from(slots.querySelectorAll("div.flex"));
    const removeIndex = rows.indexOf(row);
    const values = Array.from(slots.querySelectorAll("[data-public-link-input]"))
      .map((input) => input.value);
    values.splice(removeIndex, 1);
    renderSlots(values);
    updateAnswer();
    valueField.dispatchEvent(new Event("input", { bubbles: true }));
  });
  addButton.addEventListener("click", () => {
    const values = Array.from(slots.querySelectorAll("[data-public-link-input]"))
      .map((input) => input.value);
    if (values.length >= maxLinks) return;
    const focusIndex = values.length;
    values.push("");
    renderSlots(values, focusIndex);
  });

  renderSlots(savedLinks.length ? savedLinks : [""]);
});

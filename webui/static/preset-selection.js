// Keeps the create screen's preset rail honest as the form is edited.
//
// Three things, all of which would otherwise need a round trip per
// keystroke:
//
// 1. What the current choices differ from the preset the form started
//    from, field by field.
// 2. Whether "Update <preset>" has anything to write.
// 3. Whether these choices are already saved under some name, in which
//    case saving them again is refused and the box says which preset
//    holds them.
//
// The server computes the same three answers on render (webui/app.py
// _preset_fields, _preset_diff and _matching_preset), so a browser with
// scripting off gets the state the page was rendered with rather than a
// broken one. The field list below is the one place this file needs to
// agree with that one.
//
// Delegated on document rather than bound per-element: the size table and
// the image list are swapped by their live-catalogue toggles, and a
// re-bind step after every swap would be one more thing to forget.
(function () {
  function presetData() {
    var el = document.getElementById("preset-data");
    if (!el) return [];
    try {
      return JSON.parse(el.textContent) || [];
    } catch (error) {
      return [];
    }
  }

  // Mirrors webui/app.py _preset_fields.
  function fields(choices) {
    return {
      Profile: choices.profile,
      Size: choices.server_type,
      Location: choices.location,
      Image: choices.image,
      "Size list": choices.server_types_live ? "live" : "built-in",
      "Image list": choices.images_live ? "live" : "built-in",
    };
  }

  function currentChoices(form) {
    var data = new FormData(form);
    var custom = (data.get("image_custom") || "").trim();
    return {
      profile: data.get("profile") || "",
      server_type: data.get("server_type") || "",
      location: data.get("location") || "",
      // Free text overrides the list, exactly as the server resolves it
      // (webui/app.py _create_choices_from_form).
      image: custom || data.get("image_select") || "",
      server_types_live: Boolean(data.get("server_types_live")),
      images_live: Boolean(data.get("images_live")),
    };
  }

  function presetChoices(preset) {
    return {
      profile: preset.profile,
      server_type: preset.server_type,
      location: preset.location,
      image: preset.image,
      server_types_live: Boolean(preset.server_types_live),
      images_live: Boolean(preset.images_live),
    };
  }

  function sameChoices(a, b) {
    var left = fields(a);
    var right = fields(b);
    return Object.keys(left).every(function (key) {
      return left[key] === right[key];
    });
  }

  function differences(choices, preset) {
    var saved = fields(presetChoices(preset));
    var now = fields(choices);
    return Object.keys(saved)
      .filter(function (key) {
        return saved[key] !== now[key];
      })
      .map(function (key) {
        return { field: key, was: saved[key], now: now[key] };
      });
  }

  function findPreset(presets, name) {
    for (var i = 0; i < presets.length; i++) {
      if (presets[i].name === name) return presets[i];
    }
    return null;
  }

  function matchingPreset(choices, presets) {
    for (var i = 0; i < presets.length; i++) {
      if (sameChoices(choices, presetChoices(presets[i]))) return presets[i].name;
    }
    return null;
  }

  function renderDiff(container, changes) {
    container.textContent = "";
    changes.forEach(function (change) {
      var row = document.createElement("div");
      row.className = "diff-row";
      ["field", "was", "now"].forEach(function (part) {
        var span = document.createElement("span");
        span.className = part;
        span.textContent = change[part];
        row.appendChild(span);
      });
      container.appendChild(row);
    });
  }

  function applyChanges(choices, presets) {
    var section = document.getElementById("preset-changes");
    var baseField = document.getElementById("preset-base");
    if (!section || !baseField) return;

    var base = findPreset(presets, baseField.value);
    if (!base) {
      section.hidden = true;
      return;
    }

    var changes = differences(choices, base);
    var count = document.getElementById("preset-changes-count");
    var diff = document.getElementById("preset-diff");
    var none = document.getElementById("preset-diff-none");
    var update = document.getElementById("preset-update-button");

    section.hidden = false;
    if (count) count.textContent = String(changes.length);
    if (diff) {
      renderDiff(diff, changes);
      diff.hidden = changes.length === 0;
    }
    if (none) none.hidden = changes.length !== 0;
    if (update) update.disabled = changes.length === 0;
  }

  function applySave(choices, presets) {
    var note = document.getElementById("preset-save-note");
    var name = document.getElementById("preset-save-name");
    var button = document.getElementById("preset-save-button");
    if (!note || !name || !button) return;

    var match = matchingPreset(choices, presets);
    if (match === null) {
      note.hidden = true;
      name.disabled = false;
      button.disabled = false;
      return;
    }
    var label = document.getElementById("preset-save-match");
    if (label) label.textContent = match;
    note.hidden = false;
    name.disabled = true;
    button.disabled = true;
  }

  function apply() {
    var form = document.getElementById("create-form");
    if (!form) return;
    var presets = presetData();
    var choices = currentChoices(form);
    applyChanges(choices, presets);
    applySave(choices, presets);
  }

  document.addEventListener("change", function (event) {
    if (event.target.closest("#create-form")) apply();
  });

  document.addEventListener("input", function (event) {
    if (event.target.name === "image_custom") apply();
  });

  // A swapped-in size table or image list carries the server's idea of
  // which option is checked, which can differ from what was checked
  // before; a saved or updated preset changes what the data says.
  document.addEventListener("htmx:afterSwap", apply);
  document.addEventListener("DOMContentLoaded", apply);
})();

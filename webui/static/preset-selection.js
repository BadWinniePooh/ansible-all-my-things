// Keeps the create screen's preset selector and its save box honest as the
// form is edited.
//
// Two rules, both of which would otherwise need a round trip per keystroke:
//
// 1. The selector names the preset the form currently *is*. Editing any
//    choice moves it to Custom, because the form no longer describes that
//    preset. Arriving at a saved preset's choices by hand moves it back.
// 2. Choices that are already saved cannot be saved again: the name would
//    be taken, or a second preset would end up saying the same thing. The
//    box says which preset holds them instead.
//
// The server computes the same two answers on render (webui/app.py
// _matching_preset), so a browser with scripting off gets the state the
// page was rendered with rather than a broken one.
//
// Delegated on document rather than bound per-element: the size table and
// the image list are swapped by their live-catalogue toggles, and a re-bind
// step after every swap would be one more thing to forget.
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

  function matchingPreset(choices, presets) {
    for (var i = 0; i < presets.length; i++) {
      var preset = presets[i];
      if (
        preset.profile === choices.profile &&
        preset.server_type === choices.server_type &&
        preset.location === choices.location &&
        preset.image === choices.image &&
        Boolean(preset.server_types_live) === choices.server_types_live &&
        Boolean(preset.images_live) === choices.images_live
      ) {
        return preset.name;
      }
    }
    return null;
  }

  function apply() {
    var form = document.getElementById("create-form");
    if (!form) return;

    var match = matchingPreset(currentChoices(form), presetData());

    var select = document.getElementById("preset-select");
    if (select) select.value = match === null ? "" : match;

    var note = document.getElementById("preset-save-note");
    var name = document.getElementById("preset-save-name");
    var button = document.getElementById("preset-save-button");
    if (!note || !name || !button) return;

    if (match === null) {
      note.hidden = true;
      name.disabled = false;
      button.disabled = false;
    } else {
      var label = document.getElementById("preset-save-match");
      if (label) label.textContent = match;
      note.hidden = false;
      name.disabled = true;
      button.disabled = true;
    }
  }

  document.addEventListener("change", function (event) {
    if (event.target.closest("#create-form")) apply();

    // Picking a preset loads it straight away; the Load button beside the
    // selector is the same navigation for a browser without scripting.
    // Choosing Custom loads nothing -- it is a label for choices that
    // match no preset, not a set of choices of its own.
    if (event.target.id === "preset-select" && event.target.value) {
      event.target.form.submit();
    }
  });

  document.addEventListener("input", function (event) {
    if (event.target.name === "image_custom") apply();
  });

  // A swapped-in size table or image list carries the server's idea of
  // which option is checked, which can differ from what was checked before.
  document.addEventListener("htmx:afterSwap", apply);
  document.addEventListener("DOMContentLoaded", apply);
})();

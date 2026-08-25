// Keeps the create form's two image inputs visibly exclusive.
//
// The server resolves the image as image_custom first, falling back to the
// image_select radios (webui/app.py _create_choices_from_form), so a filled
// free-text field silently overrides whatever radio looks selected. Showing
// both as chosen would misreport what is about to be provisioned, so
// whichever one the user touched last is the only one left selected.
//
// Delegated on document rather than bound per-element: the #image-options
// fragment is swapped by the live-catalogue toggle, and a re-bind step after
// every swap would be one more thing to forget.
(function () {
  var SUPERSEDED_CLASS = "choice-superseded";
  // The radio to restore when the free-text field is emptied again, so
  // clearing a typo does not leave the form with no image at all.
  var rememberedImage = null;

  function customField() {
    return document.querySelector('[name="image_custom"]');
  }

  function imageRadios() {
    return document.querySelectorAll('[name="image_select"]');
  }

  function apply() {
    var custom = customField();
    if (!custom) return;
    var radios = imageRadios();
    if (!radios.length) return;

    var options = document.getElementById("image-options");
    var usingCustom = custom.value.trim() !== "";

    if (usingCustom) {
      Array.prototype.forEach.call(radios, function (radio) {
        if (radio.checked) rememberedImage = radio.value;
        radio.checked = false;
      });
    } else if (rememberedImage !== null) {
      Array.prototype.forEach.call(radios, function (radio) {
        if (radio.value === rememberedImage) radio.checked = true;
      });
    }

    if (options) options.classList.toggle(SUPERSEDED_CLASS, usingCustom);
  }

  document.addEventListener("input", function (event) {
    if (event.target.name === "image_custom") apply();
  });

  document.addEventListener("change", function (event) {
    // Picking from the list is the other direction of the same rule: the
    // free-text value would still have won, so it is cleared rather than
    // left behind looking active.
    if (event.target.name === "image_select") {
      var custom = customField();
      if (custom && custom.value.trim() !== "") {
        custom.value = "";
        rememberedImage = event.target.value;
        apply();
        // Assigning .value fires no input event, so the live name check
        // would keep showing a verdict for a value no longer in the field.
        custom.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
  });

  // A swapped-in #image-options arrives with the server's own idea of which
  // radio is checked, which is stale whenever free text is already present.
  document.addEventListener("htmx:afterSwap", apply);
  document.addEventListener("DOMContentLoaded", apply);
})();

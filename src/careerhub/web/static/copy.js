// Copy buttons beside an email or phone (CH-204). Bundled, no network: the clipboard API works on
// 127.0.0.1. Nothing is logged or sent. Without JavaScript the buttons stay hidden (`hidden`) and the
// text can still be selected.
(function () {
  "use strict";
  var buttons = document.querySelectorAll("button[data-copy-from]");
  Array.prototype.forEach.call(buttons, function (button) {
    if (!navigator.clipboard) { return; }  // stay hidden when copying is not possible
    button.hidden = false;
    button.addEventListener("click", function () {
      var field = document.getElementById(button.getAttribute("data-copy-from"));
      var note = button.parentNode.querySelector(".copy-note");
      if (!field || !field.value) { return; }
      navigator.clipboard.writeText(field.value).then(function () {
        if (!note) { return; }
        note.textContent = "Copied";
        window.setTimeout(function () { note.textContent = ""; }, 2000);
      }, function () {
        if (note) { note.textContent = "Could not copy"; }
      });
    });
  });
})();

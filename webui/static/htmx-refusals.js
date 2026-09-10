// A refusal is an answer, so let htmx show it.
//
// htmx swaps 2xx responses only: a 400 or a 409 fires an error event and
// leaves the page as it was. Every fragment route here answers a refusal
// with the same markup it answers success with -- the reason inside the
// rail, the banner in the same voice -- so not swapping means the user
// clicks Save and nothing at all happens.
//
// The status code stays honest for everything that is not a browser: the
// route contracts in specs/017-hcloud-web-frontend/contracts/http-routes.md
// are what the tests assert against.
(function () {
  document.addEventListener("htmx:beforeSwap", function (event) {
    var status = event.detail.xhr.status;
    if (status >= 400 && status < 500) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });
})();

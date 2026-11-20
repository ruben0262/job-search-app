// Loaded by `index.html` in its head, before anything is drawn.
(function () {
  try {
    // Dark is the default; light is a choice, kept under the same key `theme.tsx` writes.
    var choice = localStorage.getItem("theme");
    if (choice !== "light" && choice !== "dark") choice = "dark";
    document.documentElement.setAttribute("data-theme", choice);
  } catch (e) {
    /* Private mode, or storage disabled. The CSS default is dark anyway. */
  }
})();

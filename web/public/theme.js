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
  try {
    // A visitor's landing page is a file of its own (`src/App.tsx`), named by the page itself: asked for
    // now, beside the site's script. Nobody was signed in here if the mark `src/session.ts` keeps is absent.
    var landing = document.querySelector('meta[name="landing"]');
    if (landing && localStorage.getItem("signed-in") === null) {
      var ask = document.createElement("link");
      ask.rel = "modulepreload";
      ask.href = landing.content;
      document.head.appendChild(ask);
    }
  } catch (e) {
    /* As above: the site's script asks for it a moment later. */
  }
})();

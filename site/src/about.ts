/** About: the drawer's content as a page of its own, so each term can be linked (about.html#platform). */
import { $, renderReflection, renderUpdated, setFavicon } from "./chartroom";
import { aboutHtml } from "./glossary";
import { loadMeta } from "./metrics";
import { column, loadRecent } from "./recentdata";

function render(): void {
  const article = $("doc-article");
  article.innerHTML = `<h1>About</h1>${aboutHtml({ anchors: true })}`;
  // Contents: each section with its terms.
  let toc = "";
  for (const h of article.querySelectorAll<HTMLHeadingElement>("h2, h3")) {
    toc +=
      h.tagName === "H2"
        ? `<span class="doc-toc-group">${h.textContent ?? ""}</span>`
        : `<a href="#${h.id}">${h.textContent ?? ""}</a>`;
  }
  $("doc-toc").innerHTML = toc;
  // The content arrived after load, so honor a #term in the address now.
  if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
}

render();
// The strip, the masthead lines and the favicon, as on every page; the text never waits on data.
Promise.all([loadMeta(), loadRecent()])
  .then(([meta, recent]) => {
    renderUpdated(meta);
    const last7 = column(recent.batches, "unique").slice(-7).reverse();
    renderReflection(last7, "unique soundings, last 7 batches");
    setFavicon(last7);
  })
  .catch((err: unknown) => console.error(err));

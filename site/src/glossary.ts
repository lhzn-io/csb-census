/** About: what the census is and who makes it, then the glossary (docs/src/glossary.md). Drawer and page. */
import { Marked } from "marked";
import glossary from "../../docs/src/glossary.md?raw";

const REPO = "https://github.com/lhzn-io/csb-census";

const INTRO = `
<p class="about-lede">The CSB Census is an open, independent count of every crowdsourced depth sounding the
<a href="https://iho.int/en/data-centre-for-digital-bathymetry">IHO DCDB</a> has published since 2017, kept
current as <a href="https://www.ncei.noaa.gov/">NOAA NCEI</a> releases each batch.
<a href="https://longhorizon.eco/">Long Horizon Observatory</a> builds and runs it from public data; it is
not endorsed by the IHO, DCDB, NOAA or any provider.</p>
<p>How it counts is set out in the <a href="${REPO}/blob/main/docs/src/methodology.md">methodology</a>, and all of
it is <a href="${REPO}">open source</a>. The terms it uses follow.</p>
`;

const slug = (text: string): string =>
  text
    .replace(/<[^>]+>/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

/**
 * The intro and the glossary as HTML. Every heading gets an id (about.html#platform). On the page each heading
 * is also a link to itself; the drawer leaves that out, since the map pages keep the map position in the hash.
 * The glossary's own title is dropped: the drawer and the page each have theirs.
 */
export function aboutHtml(opts: { anchors: boolean }): string {
  const md = new Marked({
    renderer: {
      heading({ tokens, depth }) {
        if (depth === 1) return "";
        const text = this.parser.parseInline(tokens);
        const id = slug(text);
        const inner = opts.anchors ? `<a class="anchor" href="#${id}">${text}</a>` : text;
        return `<h${depth} id="${id}">${inner}</h${depth}>\n`;
      },
    },
  });
  // The glossary's opening paragraph points at the methodology, which the intro already does.
  // Everything up to the first section heading; [^\n]* also swallows the \r of a Windows checkout.
  const body = glossary.replace(/^# [^\n]*\n(?:(?!##)[^\n]*\n)*/, "");
  return INTRO + md.parse(body, { async: false });
}

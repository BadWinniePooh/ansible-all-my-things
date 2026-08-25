# Appearance and colours

The interface is called **Steward** in its own chrome; `webui/` stays the
package name and "Hetzner web frontend" the descriptive subtitle. It is
server-rendered with no build step, so its whole appearance is one stylesheet:
[webui/static/style.css](../../../../webui/static/style.css).

## Four colours, one place to change them

The stylesheet opens with a `:root` block holding **four colours plus a
ground**, and nothing else in the file names a colour. Every other value the
interface uses — surfaces, borders, muted text, banner washes, the text colour
on a filled button — is mixed from those five with `color-mix()`:

| Custom property | Role |
|---|---|
| `--c-ink` | Text, and every neutral surface derived from it |
| `--c-accent` | Links, the primary action, focus rings |
| `--c-positive` | Unlocked, healthy, succeeded |
| `--c-critical` | Locked, destroy, failed |
| `--ground` | The page itself: near-white in light, near-black in dark |

To restyle the interface, replace those five lines. Nothing else needs
touching, and there is no second definition of "red" left behind in a banner
or badge rule to fall out of step.

Colours are written in `oklch(L C H)` — lightness, chroma, hue. The three
accents deliberately share lightness and chroma and differ only in hue, so none
of them shouts louder than the others; keeping that when swapping is what stops
a four-colour palette from looking assembled. Two practical bounds: keep the
accents near `0.52` lightness in light mode and near `0.74` in dark so text on
their washes stays readable, and hold `--ground` within about `0.02` chroma of
neutral so the greys derived from it stay grey.

## Dark mode

A single `@media (prefers-color-scheme: dark)` block re-lights the same five
slots: `--c-ink` becomes the light foreground, `--ground` becomes the dark
page, and the three accents gain lightness. Every derived value follows
automatically, so nothing else in the stylesheet differs between the two
themes and there is no second theme to maintain.

The one deliberate exception is the run-output console, which stays dark in
both themes so playbook output reads the way it does in a terminal. Its two
colours are still derived — only the black and white poles they mix toward are
literal.

A manual light/dark switch is not offered: the container has no user
preferences to store, and the operating system already carries the choice. If
one is wanted later, it is the same five lines again under a
`[data-theme="dark"]` selector — still one block.

## Layout

A fixed sidebar carries the brand, navigation and the session lock state; the
content column carries a title bar and the page. Both live in
[webui/templates/base.html](../../../../webui/templates/base.html), which every
page extends by setting `active` to its nav key. Below 900px the sidebar
becomes a header strip and the two-column pages collapse to one column.

Icons are inline SVG in the templates, stroked on a 20px grid — no icon font,
no emoji, and nothing fetched from a third-party host, matching the same
no-outbound-access constraint that made HTMX a vendored asset.

# Mahoraga brand kit

Everything you need to show Mahoraga somewhere else: logos, icons, a member badge and the colours.
`brand-sheet.png` shows all of it on one page.

## Colours

| Name | Use it for | HEX | RGB |
|---|---|---|---|
| **Purple** | The brand colour: the wheel, buttons, links. White text on it reads at 5:1. | `#8B3DFF` | 139, 61, 255 |
| **Lilac** | The accent: purple words and highlights on a dark background, where Purple is too dark (7.5:1). | `#BA8CFF` | 186, 140, 255 |
| **Graphite** | Dark backgrounds, and text on light ones. | `#0F1013` | 15, 16, 19 |
| **Paper** | Text on Graphite, and light backgrounds. | `#F5F6F8` | 245, 246, 248 |

These are the site's own colours (`web/src/styles.css`). One purple, used flat: no gradients.

## Fonts

- **Sora SemiBold**: the name and headings. In the logo files it is already turned into shapes, so
  nobody needs the font installed to open them.
- **Inter**: everything else.

Both are free from Google Fonts.

## Which file to use

| Folder | What | Formats |
|---|---|---|
| `logo/horizontal/` | The wheel beside the name. Use this one first. | SVG, PDF, PNG, WebP, JPG |
| `logo/stacked/` | The wheel above the name, for square spaces. | SVG, PDF, PNG, WebP, JPG |
| `mark/` | The wheel alone, in purple, white and black. | SVG, PNG, WebP |
| `app-icon/` | The purple square with the white wheel, 1024 px. `-rounded` has its corners cut; `-dark` is graphite. | SVG, PNG, WebP, JPG |
| `favicon/` | The browser-tab and home-screen icons, at every size browsers ask for. | ICO, SVG, PNG |
| `member/` | The member badge and seal. | SVG, PNG, WebP |
| `social/` | A profile picture (`avatar`, 800 px), a link preview (`og-image`, 1200×630) and a header (`banner`, 1500×500). | PNG, JPG |
| `colours.png` | The four colours, for pasting into a design tool or a doc. | PNG |

Each logo comes in four colourings:

- **`on-dark`**: purple wheel, light name. Use it on Graphite or any dark background.
- **`on-light`**: purple wheel, dark name. Use it on white or any light background.
- **`white`**: all white. Use it on purple or on photos.
- **`black`**: all graphite. Use it for one-colour printing.

PNG and WebP files have transparent backgrounds. JPG has no transparency, so each JPG is already
placed on the background it was made for, with space around it.

**Which format to pick.** Use SVG or PDF wherever a site or printer accepts them: they stay sharp at
any size. Use PNG anywhere else, and JPG only where a form refuses PNG.

**Leave space around the logo.** Keep a clear margin at least half the wheel's width on every side,
and never stretch, recolour or rotate the files.

## The member badge

`member-badge` (filled purple) works on any background. The outline versions are lighter: use
`-on-dark` on Graphite and `-on-light` on white. `member-seal` is for small round places, such as
next to a name or on the corner of a profile picture. It still reads at 24 px.

On the site, paste the SVG's contents straight into the page. It is a few kilobytes and stays sharp
at any size.

## For the website

The site still uses its own `web/public/favicon.svg`. To switch to this kit, copy the `favicon/`
files into `web/public/` and add:

```html
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta property="og:image" content="https://<your domain>/og-image.png">
```

Then add these to `manifest.webmanifest`:

```json
"icons": [
  { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
  { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" },
  { "src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
]
```

## Changing anything

Every file here is made by `build.py` from one wheel, one name and the four colours at its top.
Change a value there and run it again from the repository root. It rewrites every file.

```
.venv/bin/pip install brotli uharfbuzz     # once
.venv/bin/python branding/build.py
```

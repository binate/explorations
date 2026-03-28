# Website Setup Notes

## Architecture

The website for binate-lang.dev uses two repos:

1. **binate/website** — source content, templates, and the static site generator
2. **binate/binate-lang.dev** — generated HTML output, served via GitHub Pages

This separation keeps generated artifacts out of the source repo and makes the
GitHub Pages deployment straightforward (the output repo is the Pages source).

## Static Site Generator

We wrote a custom SSG (`build.py`, ~80 lines of Python) rather than using an
existing tool (Hugo, Zola, Jekyll, etc.). Reasons:

- **Portability to binate**: the long-term goal is to port the SSG to binate
  itself ("eating our own dogfood"). Existing SSGs have enormous feature surfaces
  that would make porting impractical.
- **Simplicity**: the core is just: read files, parse frontmatter, convert
  Markdown to HTML, substitute into a template, write files. This maps cleanly
  to operations binate will support.

### Dependencies

- **mistune** (v3) for Markdown-to-HTML conversion. Chosen over python-markdown
  because mistune is a single-module, well-structured parser — conceptually
  easier to port than a plugin-heavy framework.

### How it works

1. Reads `.md` files from `content/` with `---` YAML frontmatter
2. Converts Markdown to HTML via mistune
3. Applies `templates/base.html` using simple `{{variable}}` substitution
4. Copies `static/` assets to the output directory

No plugin system, no asset pipeline, no taxonomies. Just files in, files out.

## Domain Setup

- Domain: binate-lang.dev (with www. redirect)
- DNS will point to GitHub Pages
- The output repo (binate/binate-lang.dev) will have a CNAME file for the custom domain

## Current State

The initial site is deliberately minimal — just "binate: A programming language.
More soon." — since the language itself is still in development. Content will be
added as there are things worth saying publicly.

# Hello — Reach plugin template

Minimal plugin demonstrating:

- `reach-plugin.json` manifest (schema 1)
- `ui.py` → `create_page(ctx)` GTK page
- Sidebar entry under **Plugins**

## Local install (dev)

```bash
# from Reach repo root
ID_DIR="$HOME/.local/share/reach/plugins/com__digitizable__hello"
mkdir -p "$ID_DIR"
cp -a examples/reach-plugin-hello/* "$ID_DIR/"
```

Restart Reach. Expand the left rail to see **Official** vs **Plugins**. Open **Hello**.

## Publish

1. Put this folder at the root of a GitHub repo (manifest at root).
2. Users install: Reach → **Plugins** → `owner/repo` → Install.

# flatpak-trim

Flatpak manifests often have broad permissions, defeating the purpose of sandboxing.

This script programatically strips and redfines permissions from any Flatpak.

## Run

Install dependencies:
```bash
python -m pip install -r requirements.txt
```

Optional: use a Python virtual environment (`venv`):
```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Example usage:
```sh
python flatpak_trim.py --manifest com.example.App.yaml --config config.yaml
```

Example output:

```
Manifest: /path/to/com.example.App.yaml
Permission changes:
1. [share] share=network -> REMOVED
2. [filesystem] filesystem=home -> filesystem=xdg-documents:ro
```

## Config format

The config uses one top-level `categories` map.
Each category has two optional sections:

- `remove`: list of values to remove completely
- `replace`: map of old value to new value
  - if the replacement value is `null`, that permission is removed

Example:

```yaml
categories:
  socket:
    remove:
      - x11
    replace:
      fallback-x11: wayland

  filesystem:
    remove:
      - host
      - home
    replace:
      xdg-download: xdg-documents:ro
```

## Supported categories

This script works for any `--<category>=<value>` finish-arg.

The sample config includes common permission-related categories:

- `allow`
- `device`
- `filesystem`
- `share`
- `socket`
- `talk-name`
- `system-talk-name`
- `own-name`
- `env`
- `unset-env`
- `persist`
- `add-policy`

## Tests

Run unit tests with:

```bash
python -m unittest -v tests.py
```

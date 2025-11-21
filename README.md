# google-images-cli

A CLI tool to scrape Google Images search results using Chrome DevTools Protocol (CDP).

## Requirements

- Python / uv
- Chromium or other Chromium-based browser (e.g. Brave or Chrome)

## Usage

### Option 1: Launch a new browser

```bash
uv run cli.py --launch-browser "your search query"
```

### Option 2: Connect to an existing browser

Start Chromium with remote debugging enabled:

```bash
chromium --remote-debugging-port=2102 --remote-allow-origins=*
```

Then run:

```bash
uv run cli.py "your search query"
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--endpoint` | `http://127.0.0.1:2102` | DevTools endpoint URL |
| `--launch-browser` | off | Launch a new Chromium instance |
| `--chromium-cmd` | `chromium` | Chromium executable path |
| `--profile-dir` | `profiles/main` | Browser profile directory |
| `--count` | 1 | Number of images to scrape |
| `--output-json` | - | Save results to JSON file |
| `--initial-wait` | 5.0 | Seconds to wait after page load |
| `--hover-delay` | 2.0 | Seconds to wait after hover |
| `--dump-html` | - | Save element HTML for debugging |
| `--on-finish` | `close` | Browser behavior: `close`, `keep`, or `keep-on-error` |

## Examples

Scrape 10 images and save to JSON:

```bash
uv run cli.py --launch-browser --count 10 --output-json results.json "cats"
```

Keep browser open after scraping:

```bash
uv run cli.py --launch-browser --on-finish keep "dogs"
```

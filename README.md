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
| `--output-dir` | - | Directory for `images.json` (and optional downloads) |
| `--download-images` | off | Save each `imgurl` to disk inside `--output-dir` |
| `--download-delay` | 1.0 | Seconds to wait before reusing the same host while downloading |
| `--download-user-agent` | `veilm/google-images-cli` | User-Agent header for direct downloads |
| `--annotate-images` | off | Generate `llm_alt` via OpenRouter (requires `--download-images`) |
| `--annotate-model` | `google/gemini-2.5-flash` | OpenRouter model for annotations |
| `--annotate-prompt-file` | `prompts/alt_text.md` | Prompt template for alt-text generation |
| `--annotate-timeout` | 90.0 | Seconds to wait per OpenRouter request |
| `--annotate-max-tokens` | - | Override `max_tokens` for annotation calls |
| `--annotate-referer` | - | Optional HTTP-Referer for annotation API calls |
| `--annotate-title` | - | Optional X-Title header for annotation API calls |
| `--initial-wait` | 2.5 | Seconds to wait after page load |
| `--hover-delay` | 2.0 | Seconds to wait after hover |
| `--dump-html` | - | Save element HTML for debugging |
| `--on-finish` | `close` | Browser behavior: `close`, `keep`, or `keep-on-error` |

## Examples

Scrape 10 images and save to JSON:

```bash
uv run cli.py --launch-browser --count 10 --output-dir results "cats"
```

Keep browser open after scraping:

```bash
uv run cli.py --launch-browser --on-finish keep "dogs"
```

If you also pass `--download-images`, the scraper will fetch each `imgurl` into the chosen `--output-dir`, respecting the per-host delay (default one second). The metadata in `images.json` is updated to include `downloaded` and `filename` fields for each result.

Use `--annotate-images` along with an OpenRouter API key to send each downloaded file to a multimodal model (defaults to `google/gemini-2.5-flash` with `prompts/alt_text.md`). The resulting `<alt>...</alt>` text is parsed and saved as `llm_alt` in `images.json`; any API failures are noted per entry.

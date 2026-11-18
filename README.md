# short-generator

One file (`shortgen.py`). Watches Twitch chat; when messages spike, clips ~18s of the stream
into a vertical 1080x1920 video and (optionally) titles it with Claude and uploads it as a YouTube Short.

## Setup
Needs `ffmpeg` on PATH (system install) and [uv](https://docs.astral.sh/uv/). Then:
```
uv sync                      # core
uv sync --extra ai           # + Claude titles (--model)
uv sync --extra upload       # + YouTube upload (--upload)
```
Run with `uv run shortgen.py ...`.

## Usage
```
uv run shortgen.py gaules ludwig --threshold 150 --window 30      # clip locally into ./clips
uv run shortgen.py somestreamer --detect-only --threshold 50      # tune the threshold: just logs spikes
uv run shortgen.py somestreamer --model claude-haiku-5-5          # AI title (set ANTHROPIC_API_KEY)
uv run shortgen.py somestreamer --upload --privacy private        # upload (see below)
```
| flag | default | meaning |
|---|---|---|
| `--threshold` | 100 | messages per window that trigger a clip |
| `--window` | 30 | window size (s) |
| `--cooldown` | 120 | min seconds between clips per streamer |
| `--length` / `--lead` | 18 / 13 | clip length, and seconds of video before the spike (chat reacts *after* the moment) |
| `--vertical` | blur | `blur` (blurred bg), `crop` (center crop), `none` |
| `--model` | none | Claude model id for titles |
| `--upload`, `--privacy` | off, private | YouTube upload |

Tip: run with `--detect-only` on a live stream first to see what a "normal" vs "hype" rate looks like, then set `--threshold`.

## YouTube upload
Create an OAuth "Desktop app" client in Google Cloud (YouTube Data API v3 enabled), save it as
`client_secret.json`. First `--upload` opens a browser to authorize; token is cached in `youtube_token.json`.
Note: uploads from unaudited API projects are forced to private until Google audits the project.

## How it works
Chat: anonymous Twitch IRC (no login). Video: `streamlink | ffmpeg` keeps a rolling buffer of 4s segments in `./buffer`.
On a spike it waits a few seconds, concatenates the segments around the moment, and re-encodes to 9:16.

Test: `uv run pytest`. Only Twitch is supported. Only clip streams you're allowed to re-post; check each streamer's policy.

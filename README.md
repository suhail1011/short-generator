# short-generator

One file (`shortgen.py`). Watches Twitch chat; when messages spike, clips ~18s of the stream
into a vertical 1080x1920 video and (optionally) titles it with Claude and uploads it as a YouTube Short.

## Setup
Needs `ffmpeg` on PATH, then `pip install -r requirements.txt` (anthropic/google libs only if you use those flags).

## Usage
```
python shortgen.py gaules ludwig --threshold 150 --window 30      # clip locally into ./clips
python shortgen.py somestreamer --detect-only --threshold 50      # tune the threshold: just logs spikes
python shortgen.py somestreamer --model claude-haiku-5-5          # AI title (set ANTHROPIC_API_KEY)
python shortgen.py somestreamer --upload --privacy private        # upload (see below)
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

Test: `pytest`. Only Twitch is supported. Only clip streams you're allowed to re-post; check each streamer's policy.

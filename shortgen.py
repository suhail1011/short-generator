#!/usr/bin/env python3
"""Watch Twitch streams; when chat spikes, clip ~18s and make a YouTube Short.

Per streamer: one thread reads chat (anonymous IRC), one keeps a rolling
buffer of the live video on disk (streamlink -> ffmpeg segments). A spike
(>= --threshold messages in --window seconds) cuts the buffer into a vertical
clip, optionally titles it with Claude (--model) and uploads it (--upload).
"""
import argparse
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

SEG = 4  # seconds per buffered video segment


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- detection
class SpikeDetector:
    """Fires when >= threshold messages land inside a sliding window."""

    def __init__(self, window=30, threshold=100, cooldown=120):
        self.window, self.threshold, self.cooldown = window, threshold, cooldown
        self.msgs = deque()  # (timestamp, text)
        self.last_fire = float("-inf")

    def add(self, text, now=None):
        """Record a message. Returns the window's messages if a spike fired."""
        now = time.time() if now is None else now
        self.msgs.append((now, text))
        while self.msgs and self.msgs[0][0] < now - self.window:
            self.msgs.popleft()
        if len(self.msgs) >= self.threshold and now - self.last_fire >= self.cooldown:
            self.last_fire = now
            return [t for _, t in self.msgs]
        return None


# --------------------------------------------------------------------- chat
PRIVMSG = re.compile(r"^(?:@\S+ )?:\S+ PRIVMSG #\S+ :(.*)$")


def chat_loop(channel, detector, on_spike):
    """Anonymous Twitch IRC reader; reconnects forever."""
    while True:
        try:
            raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=360)
            sock = ssl.create_default_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv")
            sock.sendall(f"PASS SCHMOOPIIE\r\nNICK justinfan{int(time.time()) % 99999}\r\nJOIN #{channel}\r\n".encode())
            log(f"[{channel}] chat connected")
            buf = b""
            while True:
                data = sock.recv(4096)
                if not data:
                    raise ConnectionError("closed")
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for line in lines:
                    line = line.decode("utf-8", "replace")
                    if line.startswith("PING"):
                        sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                    elif (m := PRIVMSG.match(line)):
                        hit = detector.add(m.group(1))
                        if hit:
                            on_spike(hit)
        except Exception as e:
            log(f"[{channel}] chat error: {e}; reconnecting in 5s")
            time.sleep(5)


# ----------------------------------------------------------------- recorder
def record_loop(channel, bufdir, keep_seconds):
    """Keep the last `keep_seconds` of the live stream as <epoch>.ts segments."""
    bufdir.mkdir(parents=True, exist_ok=True)

    def prune():
        for f in bufdir.glob("*.ts"):
            if time.time() - int(f.stem) > keep_seconds:
                f.unlink(missing_ok=True)

    while True:
        sl = subprocess.Popen(
            ["streamlink", "-O", "--twitch-disable-ads", f"twitch.tv/{channel}", "best"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ff = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c", "copy", "-f", "segment",
             "-segment_time", str(SEG), "-reset_timestamps", "1", "-strftime", "1",
             str(bufdir / "%s.ts")],
            stdin=sl.stdout)
        sl.stdout.close()
        log(f"[{channel}] recording buffer started (offline streams retry every 30s)")
        while ff.poll() is None:
            prune()
            time.sleep(5)
        sl.kill()
        time.sleep(30)


# ----------------------------------------------------------------- clipping
def make_clip(bufdir, out, spike_time, lead, length, vertical):
    """Cut [spike-lead, spike-lead+length] out of the buffer into `out`."""
    start = spike_time - lead
    segs = sorted(int(f.stem) for f in bufdir.glob("*.ts"))
    segs = [s for s in segs if start - SEG <= s <= start + length]
    if not segs:
        raise RuntimeError("no buffered video for that moment (stream offline?)")
    listfile = out.with_suffix(".txt")
    listfile.write_text("".join(f"file '{(bufdir / f'{s}.ts').resolve()}'\n" for s in segs))
    offset = max(0, start - segs[0])
    if vertical == "blur":
        vf = ("split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,boxblur=20:5[bg];[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    elif vertical == "crop":
        vf = "scale=-2:1920,crop=1080:1920"
    else:
        vf = "null"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
           "-ss", str(offset), "-t", str(length), "-vf" if vertical != "blur" else "-filter_complex", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", str(out)]
    subprocess.run(cmd, check=True)
    listfile.unlink(missing_ok=True)


# ------------------------------------------------------------- title (LLM)
def make_title(model, channel, messages):
    fallback = f"{channel} chat goes WILD"
    if not model:
        return fallback
    try:
        import anthropic  # needs ANTHROPIC_API_KEY
        sample = "\n".join(messages[-40:])[:3000]
        r = anthropic.Anthropic().messages.create(
            model=model, max_tokens=60,
            messages=[{"role": "user", "content":
                       f"Twitch streamer '{channel}' just had a moment that made chat explode. "
                       f"Recent chat:\n{sample}\n\nWrite ONE catchy YouTube Shorts title "
                       "(max 60 chars, no quotes, no hashtags). Output only the title."}])
        return r.content[0].text.strip().strip('"')[:70] or fallback
    except Exception as e:
        log(f"title generation failed ({e}); using fallback")
        return fallback


# ---------------------------------------------------------------- upload
def upload(path, title, privacy, secrets="client_secret.json", token="youtube_token.json"):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = Credentials.from_authorized_user_file(token, scopes) if os.path.exists(token) else None
    if not creds or not creds.valid:
        if creds and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(secrets, scopes).run_local_server(port=0)
        Path(token).write_text(creds.to_json())
    yt = build("youtube", "v3", credentials=creds)
    body = {"snippet": {"title": f"{title} #Shorts"[:100], "description": "#Shorts", "categoryId": "20"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    r = yt.videos().insert(part="snippet,status", body=body,
                           media_body=MediaFileUpload(str(path), resumable=True)).execute()
    log(f"uploaded: https://youtube.com/shorts/{r['id']}")


# --------------------------------------------------------------------- main
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("streamers", nargs="+", help="Twitch channel names to watch")
    p.add_argument("--threshold", type=int, default=100, help="messages per window that count as a spike (default 100)")
    p.add_argument("--window", type=int, default=30, help="spike window in seconds (default 30)")
    p.add_argument("--cooldown", type=int, default=120, help="min seconds between clips per streamer (default 120)")
    p.add_argument("--length", type=int, default=18, help="clip length in seconds (default 18)")
    p.add_argument("--lead", type=int, default=13, help="seconds of video BEFORE the spike to include (default 13; chat reacts after the moment)")
    p.add_argument("--vertical", choices=["blur", "crop", "none"], default="blur", help="9:16 conversion (default blur)")
    p.add_argument("--model", default="", help="Claude model id to write titles, e.g. claude-haiku-5-5 (needs ANTHROPIC_API_KEY); empty = no AI")
    p.add_argument("--upload", action="store_true", help="upload clips to YouTube (needs client_secret.json)")
    p.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    p.add_argument("--out", default="clips", help="output folder (default ./clips)")
    p.add_argument("--detect-only", action="store_true", help="only log spikes; no recording/clipping")
    return p.parse_args(argv)


def main():
    a = parse_args()
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    wait = a.length - a.lead + SEG + 2  # let the buffer catch up past the clip end

    def handle(channel, bufdir, hit, spike_time):
        log(f"[{channel}] SPIKE: {len(hit)} msgs in {a.window}s")
        if a.detect_only:
            return
        time.sleep(wait)
        try:
            path = out / f"{channel}_{int(spike_time)}.mp4"
            make_clip(bufdir, path, spike_time, a.lead, a.length, a.vertical)
            title = make_title(a.model, channel, hit)
            log(f"[{channel}] clip saved: {path}  title: {title}")
            if a.upload:
                upload(path, title, a.privacy)
        except Exception as e:
            log(f"[{channel}] clip failed: {e}")

    for ch in (c.lower().lstrip("#") for c in a.streamers):
        bufdir = Path("buffer") / ch
        det = SpikeDetector(a.window, a.threshold, a.cooldown)
        on_spike = lambda hit, ch=ch, bufdir=bufdir: threading.Thread(
            target=handle, args=(ch, bufdir, hit, time.time()), daemon=True).start()
        threading.Thread(target=chat_loop, args=(ch, det, on_spike), daemon=True).start()
        if not a.detect_only:
            threading.Thread(target=record_loop, args=(ch, bufdir, a.lead + wait + 60), daemon=True).start()
    log(f"watching {', '.join(a.streamers)} | threshold {a.threshold}/{a.window}s | Ctrl+C to stop")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

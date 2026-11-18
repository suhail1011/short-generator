import subprocess, time, tempfile
from pathlib import Path
from shortgen import SpikeDetector, make_clip, SEG

def test_detector():
    d = SpikeDetector(window=30, threshold=5, cooldown=60)
    assert [d.add("x", now=t) for t in (0, 1, 2, 3)] == [None] * 4
    assert d.add("x", now=4) is not None          # 5th msg in window -> fires
    assert d.add("x", now=5) is None              # cooldown
    d2 = SpikeDetector(window=10, threshold=5, cooldown=0)
    assert all(d2.add("x", now=t * 5) is None for t in range(20))  # too slow

def test_clip():
    tmp = Path(tempfile.mkdtemp()); now = int(time.time())
    for i in range(8):  # 32s of fake 16:9 video+audio
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=4",
                        "-f", "lavfi", "-i", "sine=d=4", "-c:v", "libx264", "-c:a", "aac", "-shortest",
                        str(tmp / f"{now + i * SEG}.ts")], check=True)
    for mode in ("blur", "crop", "none"):
        out = tmp / f"{mode}.mp4"
        make_clip(tmp, out, now + 20, 13, 18 if mode != "none" else 10, mode)
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                             "-of", "csv=p=0", str(out)]))
        assert 9 < dur < 19, (mode, dur)

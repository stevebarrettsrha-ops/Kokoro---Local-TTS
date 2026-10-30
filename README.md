# Kokoro Studio

A local voice-over and video studio built on [Kokoro 82M](https://github.com/hexgrad/kokoro). You write a script, direct the read with emotions, pauses and emphasis, and render a finished take. Each take comes with loudness-matched audio and SRT/VTT captions.

It has three parts that work together: **Script**, where you write and direct voice-overs; **Your voices**, which clones a voice from a recording; and **Video**, a Canva-style editor where voices, pictures, clips, music and text come together on one timeline.

Everything runs on your machine. The browser UI talks to a Python engine bound to `127.0.0.1`.

## What's different from a plain Kokoro wrapper

**Emotion is shaped inside the model.** Kokoro predicts a pitch curve and an energy curve before its vocoder turns them into audio. The studio reshapes those curves directly:

- pitch level
- intonation range (flat ↔ animated)
- rise or fall across each phrase
- a slight tremor for nervous reads
- vocal energy
- how long the model pauses at commas and full stops

This replaces the old approach of pitch-shifting finished audio, which smeared the voice. With every control at neutral the output is identical to stock Kokoro.

**Paced for video.**

- Each sentence is rendered and placed separately, so gaps between sentences and paragraphs are exact and adjustable.
- Explicit pauses are honoured to the millisecond.
- **Fit to length** hits a target duration (e.g. `0:30`) by adjusting pauses first, then pace.

**Delivered ready to edit.**

- Loudness is normalised to a LUFS target (−14 / −16 / −20 / −23) with a true-peak limiter.
- 48 kHz / 24-bit WAV, with optional MP3.
- Optional room tone so gaps aren't digitally silent.
- Light voice polish.
- Captions are timed from Kokoro's own word timings.

## Quick start (Ubuntu / Linux Mint / Debian)

1. Clone or extract this folder and open a terminal in it.
2. Start the studio, while online:

   ```bash
   ./start.sh
   ```

3. Your browser opens `http://127.0.0.1:8765`. Stop with Ctrl+C.

The first run downloads everything, so nothing has to download later. It asks before installing anything on the system itself:

- **System programs:** `ffmpeg` (Video tab, MP3 export, recordings), plus `curl` if neither curl nor wget is there to fetch uv. It installs them with `sudo apt` if you say yes; if you say no, it doesn't ask again. (Kokoro brings its own espeak-ng.)
- **uv:** fetches Python 3.12 for the studio without touching the system Python (Kokoro 0.9.4 needs Python 3.10–3.12; Ubuntu 26.04 ships 3.14). It's installed into `~/.local/bin` if you say yes; its installer also adds that folder to your shell's PATH.
- **Python packages, the spaCy English model, the Kokoro model and all 28 voices:** about 2.5 GB, into this folder.
- **Voice cloning (Chatterbox):** about 9 GB more (it's skipped if less than 12 GB is free). This installs **in the background** while you already use the studio; progress goes to `data/logs/setup-cloning.log`. Cloned voices start working when it finishes, with no restart. It keeps going if you stop the studio or close the terminal; to stop it, run `kill -- -$(cat .venv-chatterbox.installing)`. To skip it, start with `SKIP_CLONING=1 ./start.sh`.

Later starts only check that everything is still there, which takes a few seconds. If a download was interrupted, the next start carries on from where it stopped.

To install the system programs yourself instead: `sudo apt install -y curl ffmpeg` and `curl -LsSf https://astral.sh/uv/install.sh | sh`. If you start the studio from a desktop shortcut (no terminal), it can't ask, so run `./start.sh` once in a terminal first.

Python packages install into a folder-local `.venv`. If an existing `.venv` uses an unsupported Python, it is moved to a dated backup folder rather than deleted. The model and all voices download during the first `./start.sh`.

### Offline use

Run `./start.sh` once while online and let the background cloning setup finish (see `data/logs/setup-cloning.log`). After that, start with `OFFLINE=1 ./start.sh`.

### GPU

Kokoro uses CUDA automatically if PyTorch can see your NVIDIA GPU; the top bar shows the device. If it shows CPU, install a CUDA build of PyTorch into `.venv`. Take the index URL from [pytorch.org](https://pytorch.org/get-started/locally/), for example:

```bash
uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cu126
```

(System `pip` won't work here: Ubuntu blocks it, and `.venv` may not have its own pip.) Force CPU with `KOKORO_DEVICE=cpu ./start.sh`. US and UK voices now share one model in memory.

## Video editor

The **Video** tab is a Canva-style editor built into the studio. It runs entirely on your computer; ffmpeg does the final render. If the studio is stopped during an export, the export starts again by itself the next time you run `./start.sh`, and the editor picks up its progress.

- **Create a video**: pick Landscape, YouTube, YouTube Shorts, TikTok, Instagram Reel, Facebook, Instagram post or Square, and the frame is set up for you. You can change the format at any time.
- **Uploads**: drop in pictures (JPG, PNG, WebP), video (MP4, MOV including iPhone HEVC, WebM, MKV) or music (MP3, WAV, M4A…). **Record yourself** captures camera and mic straight into your uploads. Videos the browser can't play get a lightweight preview copy; the export always uses the original.
- **Voices**: every take from the Script tab, built-in or cloned, is here. **Add with captions** puts the voice on the Voice track and its words on the Text track, timed from the voice. You can also click **→ Video** after rendering, or **Add to video** in Takes.
- **Text**: presets (title, subtitle, lower third, caption, headline, quote, scripture, neon, glitch) and Canva-style effects: Shadow, Lift, Hollow, Splice, Outline, Echo, Glitch, Neon and Background.
- **Shapes**: colour blocks and overlays (dim overlay, bottom band, side panel, letterbox bars).
- **Transitions**: hover the line between two scenes on a track and click **+**, or use the panel on the right. Choose Crossfade, Dip to background, Slide left/right/up/down or Zoom. **Use for every scene change** applies one to the whole track.
- **Animations**: every picture, clip, text or shape can have an entrance and an exit (Fade, Rise, Drop, four Slides, Pop, Zoom). Title presets come animated. **Preview animation** plays just that moment.
- **Timeline**: Text, Layer 3, Layer 2 and Main tracks for visuals (higher tracks draw on top), plus Voice, Music and Sound tracks for audio. Pictures and clips you add play one after another on Main. Drag clips to move them and drag edges to trim. **S** splits, **Del** deletes, **Ctrl+D** duplicates and **Ctrl+Z** undoes. Snapping lines up clip edges and the playhead.
- **On the picture**: drag to move, use the corners to resize (Shift stretches), and guides help you centre things.
- **Properties**: position, crop or fit, opacity, fades, slow zoom (Ken Burns), volume and mute.
- **Export MP4**: H.264/AAC at the project size. You can optionally even the sound out to −14 LUFS for YouTube and social. Text, transitions and animations are computed by the same rules in the preview and the export, so the file matches what you saw.

Everything the editor makes lives in one folder inside the app, which is not committed to git:

```
creations/
  media/      your uploads (+ thumbnails and preview copies)
  projects/   saved videos (autosaved as you edit)
  exports/    finished MP4s
```

ffmpeg is required: `sudo apt install ffmpeg`.

## Directing a script

| Write | Effect |
|---|---|
| `[sad]` | Emotion for what follows, until the next direction |
| `[excited:40]` | Same, at 40 % strength (otherwise the gauge sets strength) |
| `[default]` | Back to the emotion chosen in the side panel |
| `[pause=800]`, `[pause=1.5s]` | Exact silence |
| `[beat]` | 350 ms pause, good before a reveal or cut |
| `[voice=george]` | Switch speaker for dialogue; `[voice=default]` switches back |
| `[speed=1.1]` | Relative pace for the following text |
| `*word*` | Emphasis |
| `[Yahuah](yah-HOO-ah)` | Sounds-like pronunciation for one spot; CAPS marks the stressed syllable |
| `[live](liv)` | Same, for a single word |
| `[Kokoro](/kˈOkəɹO/)` | Exact phonemes (ordinary IPA is converted for you) |
| blank line | Paragraph pause |

The 12 emotions are neutral, warm, joyful, excited, curious, confident, calm, sad, angry, fearful, dramatic and soft. Older tags (`[tender]`, `[whisper]`) still work.

### Pronunciation

Kokoro guesses unfamiliar words from their spelling. The **Pronunciation** list in the side panel fixes a word in every take. You can write the sound three ways:

- sounds-like: `liv`
- syllables, with the stressed one in CAPS: `yah-HOO-ah`
- phonemes: `/jɑhˈuɑ/`

Sounds-like spellings are converted with Kokoro's own pronunciation engine, so they behave the same way it reads ordinary words.

- **Add Hebrew names** loads about 60 Scripture and Hebrew-roots names: Yahuah, Yahusha, Elohim, Qadash, Qodesh, Shamayim, Mishlei, Torah, Shabbat, Yerushalayim and more. Every entry can be edited.
- **▷** plays a word on its own.
- **Check line at cursor** shows exactly which sounds Kokoro will use.
- **"Live"** is read as *liv* unless the next word makes it the broadcast sense (*live stream*, *live music*) or it closes a phrase like *we go live*. Override any spot with `[live](lyve)`.

**Preview line** (Ctrl+Shift+Enter) plays the sentence at the cursor, or the selected text, with the current direction, without saving a take.

### Voice blending

Pick a second voice under *Blend with*.

- **Voice** mixes the two timbres.
- **Delivery** keeps your voice's sound but borrows the other voice's rhythm and melody. Kokoro's style vector stores timbre and prosody separately, so only the prosody half is mixed.

### Your own voices (voice cloning)

Built-in Kokoro voices can't be changed into a different person, so the studio can also make a voice from a recording of a real speaker. This uses [Chatterbox](https://github.com/resemble-ai/chatterbox) by Resemble AI (MIT licence), which runs locally. A clone captures the speaker's sound, accent and way of speaking.

**Setup** happens by itself: the first `./start.sh` installs it in the background (see Quick start). You can also run `./setup-cloning.sh` yourself; it skips whatever is already done. It creates a separate environment, `.venv-chatterbox`, because Chatterbox needs different PyTorch versions from Kokoro, and downloads the model: about 6 GB of packages plus about 3 GB of model. An 8 GB NVIDIA GPU runs both engines. CPU works too, but slowly.

**Adding a voice:** in the Voice panel, choose **Add a voice from a recording…**

- Choose the sound: **Choose a file…** (any audio or video file, of any length; the first 30 minutes are opened), **Record** from your microphone, or pick a file you already uploaded in the Video tab.
- The waveform appears with the clearest 20 seconds selected. Drag across it to choose a different part, drag the green edges to fine-tune, or type the times. A click plays from that point; ▷ plays the selection.
- Choose 10–30 seconds of one person speaking naturally, with no music or other voices. The cleanest 10 seconds inside your selection go first, because they matter most.
- Only use your own voice, or a voice whose owner has given you permission.

Cloned voices appear under *Your voices*. They also work in dialogue with `[voice=Marcus]`.

**What works with cloned voices:**
- Scripts, pauses, captions, loudness and the Hebrew name pack. For cloned voices, pronunciations use the sounds-like spelling, e.g. *yah-hoo-ah*.
- Emotion buttons and the gauge set Chatterbox's emotion strength.
- Pace is approximate.
- Each read varies slightly; **Try another read** re-renders with a different take.

Recordings stay in `voices/`, which is not committed to git. Chatterbox adds an inaudible watermark to its output, as its authors intend.

### Output presets

*Made for* sets loudness, sample rate and pacing for YouTube narration, explainers, documentary, Shorts/Reels/TikTok, ads, podcasts, broadcast (EBU R128) and audiobooks. Everything stays editable afterwards.

## What gets saved

Takes go to `outputs/`:

- `.wav`, plus `.mp3` if enabled
- `.srt` and `.vtt` captions
- a `.json` file with the script and settings, so **Open script** in the Takes tab can restore a take exactly

Your current draft and theme are kept in this browser's local storage so a refresh doesn't lose work. *Save project* exports a portable `.json`.

## Project layout

- `app.py`: Flask routes (local-only, rejects non-localhost Host headers)
- `studio/engine.py`: rendering, voice blending, pacing, fit-to-length
- `studio/prosody.py`: in-model pitch/energy/pause shaping
- `studio/styles.py`: emotion and output presets
- `studio/script.py`: script notation parser
- `studio/phonemes.py`: sounds-like respelling, IPA clean-up, Hebrew name pack, *live* fix
- `studio/audio.py`: loudness (ITU BS.1770), limiter, polish, room tone
- `studio/captions.py`: SRT/VTT builder
- `studio/cloning.py`, `cloning/chatterbox_worker.py`, `setup-cloning.sh`: cloned voices (Chatterbox in its own environment)
- `studio/video.py`: media library, video projects, transitions/animation plan, ffmpeg export
- `static/`: the web UI (`video.js` and `video.css` hold the editor)
- `tests/`: `uv pip install --python .venv/bin/python -r requirements-dev.txt && .venv/bin/python -m pytest -q`. The suite uses a fake voice, so it needs no model download. `test_prosody.py` runs Kokoro's real model code with random weights when Kokoro is installed.

## Limits

Kokoro is not an acting model. The studio can make a read sadder, brighter, tenser or calmer, but it cannot laugh, cry, shout or truly whisper. Moderate intensity (the gauge's green zone) sounds the most natural. Very short lines are merged with their neighbour because Kokoro is less stable on them.

## Troubleshooting

- **Updated to a new version and it wants to download everything again**: the new files probably landed in a different folder from your old install. From the new folder, run `./move-old-setup.sh /path/to/old/folder` to move your Python environment, downloaded models, takes, voices and videos across, then run `./start.sh`.
- **`Permission denied` when running a script**: run `chmod +x *.sh` once, or start with `bash start.sh`.
- **Old `.venv-incompatible-*` folders**: backups made when an old Python environment was replaced. Once the studio runs, you can delete them to free several GB.
- **Cloned voices fail with "no kernel image" or run out of GPU memory**: cloning pins its own older PyTorch, which may not support the newest GPUs. Run it on the CPU with `CHATTERBOX_DEVICE=cpu ./start.sh` (slower, but it works).

- **Engine not running**: start `./start.sh` and reload the page.
- **spaCy "No package installer found"**: stop the server and run `./repair-spacy.sh`, then `./start.sh`.
- **`Requires-Python >=3.10,<3.13`** or **"No supported Python interpreter"**: run `./start.sh` in a terminal and answer yes to installing uv, or install it yourself with `curl -LsSf https://astral.sh/uv/install.sh | sh`, then run `./start.sh` again.
- **MP3 option greyed out**: install `ffmpeg`.
- **Port in use**: `KOKORO_PORT=8899 ./start.sh`.
- **Settings you can pass to `./start.sh`**: `KOKORO_PORT` (default 8765), `KOKORO_DEVICE` (`cpu` or `cuda`), `OFFLINE=1`, `SKIP_CLONING=1` (don't install voice cloning; `true`/`yes` work too), `CHATTERBOX_DEVICE` (`cpu` or `cuda`, for cloned voices) and `CHATTERBOX_PYTHON` (use a Chatterbox environment somewhere else).

## Privacy

Single-user and local only. The server listens on loopback and refuses requests addressed to any other host name. Don't forward its port. Model and voice licences belong to their owners; check them before commercial use.

# Kokoro Studio

A local voice-over studio for [Kokoro 82M](https://github.com/hexgrad/kokoro), built for narration and video. You write a script, direct the read with emotions, pauses and emphasis, and render a finished take. Each take comes with loudness-matched audio and SRT/VTT captions.

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
2. Install system dependencies once. **Kokoro 0.9.4 needs Python 3.10–3.12**; on Ubuntu 26.04 use `uv` to get an app-only Python 3.12:

   ```bash
   sudo apt update
   sudo apt install -y curl espeak-ng libsndfile1 ffmpeg
   curl -LsSf https://astral.sh/uv/install.sh | sh
   export PATH="$HOME/.local/bin:$PATH"
   uv python install 3.12
   ```

   `ffmpeg` is optional and only needed for MP3 export.

3. Start the studio:

   ```bash
   ./start.sh
   ```

4. Your browser opens `http://127.0.0.1:8765`. Stop with Ctrl+C.

Python packages install into a folder-local `.venv`. If an existing `.venv` uses an unsupported Python, it is moved to a dated backup folder rather than deleted. The model and voices download from Hugging Face on first use, so the first render needs internet.

### Offline use

While online, run `./start.sh` once, stop it, then run `./download-models.sh`. After that, start with `OFFLINE=1 ./start.sh`.

### GPU

Kokoro uses CUDA automatically if PyTorch can see your NVIDIA GPU; the top bar shows the device. Install a CUDA build of PyTorch into `.venv` following [pytorch.org](https://pytorch.org/get-started/locally/) if it shows CPU. Force CPU with `KOKORO_DEVICE=cpu ./start.sh`. US and UK voices now share one model in memory.

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
| `[Kokoro](/kˈOkəɹO/)` | Exact pronunciation (Kokoro phoneme notation) |
| blank line | Paragraph pause |

The 12 emotions are neutral, warm, joyful, excited, curious, confident, calm, sad, angry, fearful, dramatic and soft. Older tags (`[tender]`, `[whisper]`) still work.

The **Pronunciation** list in the side panel fixes brand names and jargon everywhere (`SQL → sequel`, or phonemes between slashes).

**Preview line** (Ctrl+Shift+Enter) plays the sentence at the cursor, or the selected text, with the current direction, without saving a take.

### Voice blending

Pick a second voice under *Blend with*.

- **Voice** mixes the two timbres.
- **Delivery** keeps your voice's sound but borrows the other voice's rhythm and melody. Kokoro's style vector stores timbre and prosody separately, so only the prosody half is mixed.

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
- `studio/audio.py`: loudness (ITU BS.1770), limiter, polish, room tone
- `studio/captions.py`: SRT/VTT builder
- `static/`: the web UI
- `tests/`: `pip install -r requirements-dev.txt && pytest -q`. The suite uses a fake voice, so it needs no model download. `test_prosody.py` runs Kokoro's real model code with random weights when Kokoro is installed.

## Limits

Kokoro is not an acting model. The studio can make a read sadder, brighter, tenser or calmer, but it cannot laugh, cry, shout or truly whisper. Moderate intensity (the gauge's green zone) sounds the most natural. Very short lines are merged with their neighbour because Kokoro is less stable on them.

## Troubleshooting

- **Engine not running**: start `./start.sh` and reload the page.
- **spaCy "No package installer found"**: stop the server and run `./repair-spacy.sh`, then `./start.sh`.
- **`Requires-Python >=3.10,<3.13`**: install Python 3.12 with `uv` as above and rerun `./start.sh`.
- **MP3 option greyed out**: install `ffmpeg`.
- **Port in use**: `KOKORO_PORT=8899 ./start.sh`.

## Privacy

Single-user and local only. The server listens on loopback and refuses requests addressed to any other host name. Don't forward its port. Model and voice licences belong to their owners; check them before commercial use.

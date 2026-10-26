#!/usr/bin/env python3
"""Chatterbox worker. Runs inside .venv-chatterbox (see ../setup-cloning.sh).

Protocol: one JSON object per line on stdin, one JSON reply per line on
stdout. All library logging goes to stderr so it can't corrupt the replies.
"""
import json
import os
import sys

PROTOCOL = sys.stdout
sys.stdout = sys.stderr  # libraries print freely; keep stdout for replies

import numpy as np  # noqa: E402
import torch  # noqa: E402

MODEL = None
CURRENT_REF = None


def reply(**data):
    PROTOCOL.write(json.dumps(data) + '\n')
    PROTOCOL.flush()


def load():
    global MODEL
    if MODEL is None:
        from chatterbox.tts import ChatterboxTTS
        device = os.environ.get('CHATTERBOX_DEVICE') or ('cuda' if torch.cuda.is_available() else 'cpu')
        print(f'[cloning] loading Chatterbox on {device}…', flush=True)
        MODEL = ChatterboxTTS.from_pretrained(device=device)
        print('[cloning] ready', flush=True)
    return MODEL


def tts(req):
    global CURRENT_REF
    model = load()
    ref = req['ref']
    exaggeration = float(req.get('exaggeration', 0.5))
    stamp = (ref, os.path.getmtime(ref))
    if stamp != CURRENT_REF:
        model.prepare_conditionals(ref, exaggeration=exaggeration)
        CURRENT_REF = stamp
    seed = int(req.get('seed', 0))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    wav = model.generate(req['text'], exaggeration=exaggeration, cfg_weight=float(req.get('cfg_weight', 0.5)),
                         temperature=float(req.get('temperature', 0.8)))
    y = wav.squeeze().detach().cpu().numpy().astype(np.float32)
    np.save(req['out'], y)
    return {'sr': model.sr, 'samples': int(y.size)}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get('op') == 'tts':
                reply(ok=True, **tts(req))
            elif req.get('op') == 'load':
                load()
                reply(ok=True)
            else:
                reply(ok=False, error='unknown op')
        except Exception as exc:  # noqa: BLE001 - report every failure to the studio
            msg = str(exc)
            if 'out of memory' in msg.lower():
                msg = 'The GPU ran out of memory while cloning. Close other GPU apps, or set CHATTERBOX_DEVICE=cpu.'
            reply(ok=False, error=f'{type(exc).__name__}: {msg}'[:600])


if __name__ == '__main__':
    main()

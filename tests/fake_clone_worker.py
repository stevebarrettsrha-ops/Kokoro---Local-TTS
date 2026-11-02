"""Stands in for cloning/chatterbox_worker.py: same pipe protocol, tone audio."""
import json
import sys

import numpy as np

for line in sys.stdin:
    req = json.loads(line)
    if req.get('text') == 'CRASH':
        sys.exit(3)
    text = req['text']
    if 'Dropped' in text and '. ' in text:
        text = text.split('. ')[0]  # act like Chatterbox ending a long take early
    n = int(24000 * (0.1 + len(text) / 15))
    t = np.arange(n) / 24000
    y = (0.2 * np.sin(2 * np.pi * (110 + 100 * req['exaggeration']) * t)).astype(np.float32)
    y[:2400] = 0
    np.save(req['out'], y)
    print(json.dumps({'ok': True, 'sr': 24000, 'samples': n, 'echo': req}), flush=True)

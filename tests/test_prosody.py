"""Checks the in-model prosody hooks against Kokoro's real model code.

Uses a randomly initialised KModel (no download needed), so it verifies the
plumbing, not the sound. Skipped when kokoro/torch are not installed.
"""
import sys
import tempfile
from pathlib import Path

import pytest

torch = pytest.importorskip('torch')
km = pytest.importorskip('kokoro.model')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.prosody import Shape, patch_model, shaping  # noqa: E402

CHARS = list("abcdefghijklmnopqrstuvwxyz ,.!?;:—…ˈˌəɪʊ")
VOCAB = {c: i + 1 for i, c in enumerate(CHARS)}
CONFIG = {
    "istftnet": {"upsample_kernel_sizes": [20, 12], "upsample_rates": [10, 6], "gen_istft_hop_size": 5,
                 "gen_istft_n_fft": 20, "resblock_dilation_sizes": [[1, 3, 5], [1, 3, 5], [1, 3, 5]],
                 "resblock_kernel_sizes": [3, 7, 11], "upsample_initial_channel": 512},
    "dim_in": 64, "dropout": 0.2, "hidden_dim": 512, "max_conv_dim": 512, "max_dur": 50, "multispeaker": True,
    "n_layer": 3, "n_mels": 80, "n_token": 178, "style_dim": 128, "text_encoder_kernel_size": 5,
    "plbert": {"hidden_size": 768, "num_attention_heads": 12, "intermediate_size": 2048,
               "max_position_embeddings": 512, "num_hidden_layers": 12, "dropout": 0.1},
    "vocab": VOCAB,
}
PS = ''.join(c for c in "hˈɛlo, wɝld. haʊ ɑɹ ju?" if c in VOCAB)


@pytest.fixture(scope='module')
def model():
    empty = tempfile.mktemp(suffix='.pth')
    torch.save({}, empty)
    return km.KModel(repo_id='hexgrad/Kokoro-82M', config=CONFIG, model=empty).eval()


def run(m, shape=None):
    torch.manual_seed(0)
    ref = torch.ones(1, 256) * 0.1
    with shaping(shape):
        return m(PS, ref, 1.0, return_output=True)


def test_patch_is_transparent_when_neutral(model):
    stock = run(model)
    assert patch_model(model)
    assert torch.allclose(stock.audio, run(model, None).audio, atol=1e-5)
    assert torch.allclose(stock.audio, run(model, Shape()).audio, atol=1e-5)


def test_f0_energy_and_pause_shaping(model):
    patch_model(model)
    seen = {}
    decode = model.decoder.forward

    def spy(asr, f0, n, s):
        seen['f0'], seen['n'] = f0.clone(), n.clone()
        return decode(asr, f0, n, s)

    model.decoder.forward = spy
    original = model.predictor.F0Ntrain

    def realistic(en, s):  # random weights give no usable F0; supply a voiced contour
        f0, n = original(en, s)
        idx = torch.arange(f0.shape[-1], dtype=f0.dtype)
        f0 = (180 + 30 * torch.sin(idx / 9.0)).unsqueeze(0)
        f0[..., :8] = 0
        f0[..., -8:] = 0
        return f0, n

    model.predictor.F0Ntrain = realistic
    try:
        run(model, Shape())
        base_f0, base_n = seen['f0'], seen['n']
        voiced = base_f0 > 100
        run(model, Shape(pitch=12))
        assert float((seen['f0'][voiced] / base_f0[voiced]).mean()) == pytest.approx(2.0, abs=0.01)
        assert torch.all(seen['f0'][base_f0 == 0] == 0)
        run(model, Shape(range=2.0))
        wide = seen['f0'][voiced].std()
        run(model, Shape(range=0.5))
        assert wide > base_f0[voiced].std() > seen['f0'][voiced].std()
        run(model, Shape(tilt=-3))
        st = torch.log2(seen['f0'][voiced] / base_f0[voiced]) * 12
        assert st[:5].mean() > 1 and st[-5:].mean() < -1
        run(model, Shape(energy=0.5))
        assert float((seen['n'] - base_n).max()) > 0.1
        # Tilt follows each sentence: the ramp restarts after the full stop.
        run(model, Shape(tilt=-3)); per = torch.log2(seen['f0'] / base_f0.clamp(min=1)) * 12
        stop_frame = int(torch.cumsum(run(model, Shape()).pred_dur, 0)[PS.index('.') + 1]) * 2
        assert float(per[0, stop_frame + 4]) > 0 > float(per[0, stop_frame - 4])
        d0 = run(model, Shape()).pred_dur
        d_stop = run(model, Shape(stop_ms=250)).pred_dur
        i = PS.index('.') + 1
        assert int(d_stop[i]) == int(d0[i]) + 10 and int(d_stop.sum()) == int(d0.sum()) + 10
        d1, d2 = run(model, Shape()).pred_dur, run(model, Shape(punct=2.0)).pred_dur
        punct = torch.tensor([c in ',.!?;:—…' for c in ' ' + PS + ' '])
        assert torch.all(d2[punct] >= d1[punct] * 2 - 1) and torch.equal(d1[~punct], d2[~punct])
    finally:
        model.predictor.F0Ntrain = original
        model.decoder.forward = decode

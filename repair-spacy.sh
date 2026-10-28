#!/usr/bin/env bash
# Install the English spaCy pipeline explicitly, preventing Kokoro/Misaki from
# attempting an implicit model download when the user presses Generate.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH"
PY="$PWD/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo 'ERROR: .venv is missing. Run ./start.sh to create it.' >&2
  exit 1
fi
if "$PY" -c 'import spacy; spacy.load("en_core_web_sm")' >/dev/null 2>&1; then
  echo 'spaCy English model (en_core_web_sm): ready'
  exit 0
fi
case "${OFFLINE:-0}" in 1|true|yes|TRUE|YES) OFFLINE=1 ;; *) OFFLINE=0 ;; esac
if [ "$OFFLINE" = '1' ]; then
  echo 'ERROR: spaCy English model is missing; install once while online with ./repair-spacy.sh' >&2
  exit 1
fi
# Model versions must match the installed spaCy major.minor series.
spacy_branch="$("$PY" -c 'from importlib.metadata import version; v=version("spacy").split("."); print(".".join(v[:2]))')" || {
  echo 'ERROR: spaCy is missing. Reinstall the app requirements and retry.' >&2
  exit 1
}
case "$spacy_branch" in
  3.8) model_version=3.8.0 ;;
  3.7) model_version=3.7.1 ;;
  *) echo "ERROR: Unsupported spaCy $spacy_branch. This repair script handles spaCy 3.7/3.8; check ./.venv/bin/python -m spacy info." >&2; exit 1 ;;
esac
wheel="https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-${model_version}/en_core_web_sm-${model_version}-py3-none-any.whl"
echo "Installing spaCy English model ${model_version} for spaCy ${spacy_branch} (internet required)..."
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" "$wheel"
else
  # Python environments created by 'uv venv' may not contain pip by default.
  if ! "$PY" -m pip --version >/dev/null 2>&1; then
    "$PY" -m ensurepip --upgrade || {
      echo 'ERROR: Neither uv nor pip is available. Install uv and retry.' >&2
      exit 1
    }
  fi
  "$PY" -m pip install "$wheel"
fi
"$PY" - <<'PYCODE'
import spacy
nlp = spacy.load('en_core_web_sm')
assert nlp('Kokoro is ready.')[0].text == 'Kokoro'
print('spaCy English model: verified. Restart the studio and retry generation.')
PYCODE

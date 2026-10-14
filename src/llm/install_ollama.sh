#!/usr/bin/env bash
# User-space install of Ollama (no sudo) + pull a small coder model.
#   ./llm/install_ollama.sh [model]        default model: qwen2.5-coder:1.5b
# Installs to $OLLAMA_HOME (default ~/ollama); starts a server on 127.0.0.1:11434.
# Needs: curl, python3 >= 3.14 (reads .tar.zst) or the zstd binary.
set -euo pipefail
MODEL=${1:-qwen2.5-coder:1.5b}
OLLAMA_HOME=${OLLAMA_HOME:-$HOME/ollama}
mkdir -p "$OLLAMA_HOME"
cd "$OLLAMA_HOME"

if [ ! -x bin/ollama ]; then
  TAG=$(curl -fsSL https://api.github.com/repos/ollama/ollama/releases/latest | python3 -c "import json,sys;print(json.load(sys.stdin)['tag_name'])")
  echo "downloading ollama $TAG ..."
  curl -fL --retry 3 -o ollama.tar.zst "https://github.com/ollama/ollama/releases/download/$TAG/ollama-linux-amd64.tar.zst"
  echo "extracting ..."
  if command -v zstd >/dev/null; then
    tar --zstd -xf ollama.tar.zst
  else
    python3 - <<'EOF'
import tarfile
from compression import zstd          # Python 3.14+
with zstd.open("ollama.tar.zst", "rb") as f, tarfile.open(fileobj=f, mode="r|") as t:
    t.extractall(".")
EOF
  fi
  rm -f ollama.tar.zst
fi

export OLLAMA_MODELS="$OLLAMA_HOME/models"
if ! curl -fs http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  nohup "$OLLAMA_HOME/bin/ollama" serve > "$OLLAMA_HOME/serve.log" 2>&1 &
  for i in $(seq 1 30); do curl -fs http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
fi
echo "server up: $(curl -fs http://127.0.0.1:11434/api/version)"
"$OLLAMA_HOME/bin/ollama" pull "$MODEL"
"$OLLAMA_HOME/bin/ollama" list
echo "READY  export LLM_BASE_URL=http://127.0.0.1:11434/v1 LLM_MODEL=$MODEL"

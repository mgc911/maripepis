#!/usr/bin/env bash
# Descarga una voz Piper en español (medium) en models/piper/.
set -euo pipefail

DEST="models/piper"
VOICE="es_ES-sharvard-medium"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/sharvard/medium"

mkdir -p "$DEST"

echo "⬇️  Descargando voz Piper: $VOICE"
curl -L --fail -o "$DEST/$VOICE.onnx"      "$BASE/$VOICE.onnx"
curl -L --fail -o "$DEST/$VOICE.onnx.json" "$BASE/$VOICE.onnx.json"

echo "✅ Voz lista en $DEST/$VOICE.onnx"
echo "   (coincide con [tts].voice de config.toml)"

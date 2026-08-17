Telegram command surface for the Option 5 ComfyUI bridge

Purpose
- Give Hermes a predictable, bounded command contract when operating from Telegram chats
- Keep generation safe and legible instead of accepting arbitrary shell-like input

Recommended commands

1. generate
Meaning
- txt2img generation from a prompt

Recommended mapping
- bridge command:
  python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py command --text "generate ..." --send-telegram

Allowed bounded args
- prompt
- negative prompt
- width
- height
- steps
- cfg
- seed
- checkpoint

2. img2img
Meaning
- use an uploaded image plus a prompt

Recommended mapping
- bridge command:
  python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py command --text "img2img ..." --input-image /path/to/downloaded/image.png --send-telegram

Allowed bounded args
- prompt
- negative prompt
- width
- height
- steps
- cfg
- seed
- denoise
- checkpoint

3. send latest
Meaning
- forward the newest unsent output file from the watch directory

Recommended mapping
- bridge command:
  python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py send-latest

4. resend file
Meaning
- re-send a chosen file even if duplicate suppression already marked it as sent

Recommended mapping
- bridge command:
  python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py send-file /absolute/path --allow-resend

5. status
Meaning
- show bridge status and live ComfyUI reachability

Recommended mapping
- bridge command:
  python3 __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/comfyui_bridge.py status

6. help
Meaning
- return this file plus the main README summary

Safe argument handling rules
- Prefer explicit commands over free-form shell syntax
- Map Telegram text only into known bridge arguments
- Do not pass arbitrary JSON from Telegram unless it is trusted and validated first
- Keep width/height/steps/cfg inside the bridge validation bounds
- Keep presets limited to supported workflows unless the user deliberately requests a custom workflow file

Conversational defaults
- Plain text prompt can be treated as generate txt2img if the context clearly asks for image generation
- Uploaded image plus prompt can be treated as img2img
- For ambiguous messages, require explicit command intent in the chat

Error reply shapes to return in Telegram
- ComfyUI offline
- Workflow file missing
- Missing checkpoint
- Missing custom node
- Missing LoRA or VAE
- Generation complete
- File sent

Notes
- This command contract relies on Hermes already being able to receive and respond through Telegram
- The bridge handles generation, file readiness, duplicate suppression, and Telegram file delivery

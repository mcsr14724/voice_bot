# STT and TTS dataset evaluation

The backend provides authenticated endpoints for evaluating paired WAV and transcript files. They are disabled by default in every environment; set `VOICE_BOT_ENABLE_AUDIO_EVALUATION=true` to enable them, including in production.

## Enable locally

Start the backend from the repository root with the usual local environment and add:

```powershell
$env:VOICE_BOT_ENABLE_AUDIO_EVALUATION = "true"
fastapi dev backend\main.py
```

The account used for the request must be signed in, and its bearer access token must be supplied. Configure Deepgram for both endpoints. Configure Azure Speech as well for TTS evaluation.

Endpoints:

- `POST /api/v1/audio-evaluation/stt`
- `POST /api/v1/audio-evaluation/tts`

For each request, upload exactly one `.wav` and one UTF-8 `.txt`. They must have the same filename stem, for example `meeting-01.wav` and `meeting-01.txt`. Evaluation uses Deepgram's prerecorded-audio endpoint, not the live streaming websocket; the full normalized WAV is sent as one file for transcription. The folder UI detects `ar`, `bn`, `de`, `en`, `es`, `fr`, `hi`, `ta`, and `te` language folders. For non-Latin scripts it checks the transcript text and uses that script's language if it conflicts with the folder name.

Upload your original WAV and its matching UTF-8 `.txt` transcript; you do not need to convert the audio first. The endpoint converts uncompressed PCM WAV files (8-, 16-, 24-, or 32-bit; 1–8 channels; 8–96 kHz) to mono 16 kHz PCM for Deepgram. Audio must be at most 90 seconds. Per-file and total upload limits are enforced. Compressed WAV formats and non-WAV audio are not supported. Transcript files must contain the expected words, not labels or metadata.

## STT example

From PowerShell, with a token from your signed-in local app:

```powershell
$headers = @{ Authorization = "Bearer $token" }
curl.exe -X POST http://127.0.0.1:8000/api/v1/audio-evaluation/stt `
  -H "Authorization: Bearer $token" `
  -F "audio_file=@C:\audio-set\clip-01.wav" `
  -F "transcript_file=@C:\audio-set\clip-01.txt" `
  -F "language=te-IN"
```

The optional `language` field accepts `ar-EG`, `bn-IN`/`bn-BD`, `de-DE`, `en-IN`, `es-ES`, `fr-FR`, `hi-IN`, `ta-IN`, or `te-IN`. It maps each to a language supported by Nova-3 and overrides general live-chat language hints. When omitted, the endpoint uses `VOICE_BOT_DEEPGRAM_LANGUAGE` and its configured hints. The JSON response contains the selected recognition language, recognized transcript, and scores for that pair. **Word error rate (WER)** is the standard primary ASR metric:

```text
WER = (substitutions + deletions + insertions) / reference word count
```

The response also includes word accuracy (`max(0, 1 - WER) * 100`) for a more intuitive display, and character error rate (CER) as a secondary metric. Punctuation and case are normalized, Unicode combining marks are retained, and digit sequences such as "four eight two nine one" versus "48291" are normalized for the selected language. WER can exceed 100% when there are many insertions.

## TTS example

The TXT file is the text to synthesize; the same-stem WAV is a human/reference recording of that text. The response is a ZIP containing `summary.json` and one `*.generated.wav`.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/audio-evaluation/tts `
  -H "Authorization: Bearer $token" `
  -F "reference_audio_file=@C:\audio-set\clip-01.wav" `
  -F "transcript_file=@C:\audio-set\clip-01.txt" `
  -o tts-evaluation.zip
```

`summary.json` reports Deepgram WER for the reference recording and generated recording against the expected TXT, WER between their recognized transcripts, plus generated WAV sample rate, channel count, duration, RMS, peak level, and a silent/very-quiet warning. The archive contains the one generated WAV, in mono 24 kHz PCM matching the app's Azure Speech output format.

WER/CER validate **recognized content**, not voice naturalness, emotion, pronunciation quality in every context, or similarity to the reference speaker. The evaluation uses Deepgram with the language inferred from the folder and transcript script; live voice may use Gemini Live or the Deepgram streaming path, so the results do not directly score the live conversation architecture. For TTS, the evaluator selects an Azure neural voice for the detected language. Raw waveform equality, MSE, and correlation are not appropriate for comparing two independently spoken versions: timing, voice, and prosody differ. Listen to the generated WAV files for pronunciation, prosody, clipping, and intelligibility. For rigorous voice-quality benchmarking, use a separately designed listening study or a validated speech-quality metric with its required conditions.

## Limits and safety

These endpoints send uploaded reference and generated audio to configured Deepgram and Azure Speech services. Use synthetic or consented recordings only. Endpoints require a valid authenticated user and `VOICE_BOT_ENABLE_AUDIO_EVALUATION=true`; they return 404 when the flag is false. Enabling the flag in production allows authenticated accounts to use the configured provider credentials and incur provider usage. Keep the flag off when evaluation is not needed.

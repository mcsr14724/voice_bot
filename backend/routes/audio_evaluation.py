"""Authenticated development-only STT and TTS dataset evaluation endpoints."""

import io
import json
import re
import struct
import wave
import zipfile
from collections.abc import AsyncIterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from websockets.exceptions import WebSocketException

from backend.core.config import Settings, get_settings
from backend.core.exceptions import ApplicationError
from backend.core.stt.evaluation import (
    MAX_AUDIO_FILE_BYTES,
    MAX_TOTAL_UPLOAD_BYTES,
    MAX_TRANSCRIPT_FILE_BYTES,
    audio_frames,
    decode_wav_to_16khz_mono,
    end_audio_queue,
    new_audio_queue,
    score_transcript,
    transcript_tokens,
)
from backend.core.stt.transcription import DeepgramTranscriber
from backend.core.tts.protocol import SpeechSynthesizer
from backend.models.auth import UserResponse
from backend.routes.auth import get_current_user

router = APIRouter(prefix="/audio-evaluation", tags=["audio evaluation"])
MAX_SYNTHESIZED_AUDIO_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARACTERS = 4_000
DEEPGRAM_ERROR_CODES = {
    "provider_error",
    "credentials_rejected",
    "rate_limited",
    "request_rejected",
    "provider_unavailable",
    "connection_timeout",
    "connection_failed",
}


def _check_evaluation_enabled(settings: Settings) -> None:
    if not settings.enable_audio_evaluation or settings.environment == "production":
        raise HTTPException(status_code=404, detail="Audio evaluation is not enabled")


def _get_speech_synthesizer(request: Request) -> SpeechSynthesizer:
    synthesizer = getattr(request.app.state, "speech_synthesizer", None)
    if synthesizer is None:
        raise ApplicationError(
            "Text-to-speech is not configured", status_code=503, code="tts_unavailable"
        )
    return synthesizer


def _get_transcriber(settings: Settings) -> DeepgramTranscriber:
    if settings.deepgram_api_key is None:
        raise ApplicationError(
            "Speech-to-text is not configured", status_code=503, code="stt_unavailable"
        )
    return DeepgramTranscriber(
        settings.deepgram_api_key.get_secret_value(),
        language=settings.deepgram_language,
        language_hints=settings.deepgram_language_hints,
    )


def _filename_stem(upload: UploadFile, extension: str) -> str:
    filename = upload.filename
    if not filename:
        raise HTTPException(status_code=422, detail="Every upload must have a filename")
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not basename.lower().endswith(extension):
        raise HTTPException(
            status_code=422,
            detail=f"Expected a {extension} file; received an invalid filename",
        )
    stem = basename[: -len(extension)]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", stem):
        raise HTTPException(status_code=422, detail="Upload filename has an invalid sample name")
    return stem


async def _read_limited(upload: UploadFile, limit: int, label: str) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"{label} exceeds the upload size limit")
    return data


async def _load_pair(
    audio_upload: UploadFile,
    transcript_upload: UploadFile,
) -> tuple[str, bytes, str]:
    stem = _filename_stem(audio_upload, ".wav")
    transcript_stem = _filename_stem(transcript_upload, ".txt")
    if stem != transcript_stem:
        raise HTTPException(
            status_code=422,
            detail="The WAV and TXT files must have the same filename stem",
        )

    audio_bytes = await _read_limited(audio_upload, MAX_AUDIO_FILE_BYTES, "WAV file")
    transcript_bytes = await _read_limited(
        transcript_upload, MAX_TRANSCRIPT_FILE_BYTES, "Transcript file"
    )
    if len(audio_bytes) + len(transcript_bytes) > MAX_TOTAL_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Total upload size exceeds 50 MiB")
    try:
        transcript = transcript_bytes.decode("utf-8-sig").strip()
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=422, detail=f"Transcript for {stem} must be UTF-8 text"
        ) from error
    if not transcript or not transcript_tokens(transcript):
        raise HTTPException(status_code=422, detail=f"Transcript for {stem} is empty")
    if len(transcript) > MAX_TEXT_CHARACTERS:
        raise HTTPException(
            status_code=413, detail=f"Transcript for {stem} exceeds 4,000 characters"
        )
    return stem, audio_bytes, transcript


async def _transcribe_wav(
    audio_bytes: bytes,
    *,
    transcriber: DeepgramTranscriber,
) -> str:
    try:
        pcm_bytes, _ = decode_wav_to_16khz_mono(audio_bytes)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    events: list[dict[str, Any]] = []

    async def collect(event: dict[str, Any]) -> None:
        events.append(event)

    queue = new_audio_queue()
    end_audio_queue(queue, audio_frames(pcm_bytes))
    try:
        await transcriber.consume(queue, collect)
    except (TimeoutError, OSError, WebSocketException) as error:
        code = transcriber.connection_error_code(error)
        raise ApplicationError(
            f"Speech provider connection failed: {code}",
            status_code=502,
            code=code,
        ) from None

    provider_errors = [
        event["code"]
        for event in events
        if event.get("type") == "transcription.error"
        and event.get("code") in DEEPGRAM_ERROR_CODES
    ]
    if provider_errors:
        raise ApplicationError(
            f"Speech provider error: {provider_errors[-1]}",
            status_code=502,
            code=provider_errors[-1],
        )
    final_text = [
        str(event["text"])
        for event in events
        if event.get("type") == "transcript"
        and event.get("is_final")
        and isinstance(event.get("text"), str)
        and event["text"].strip()
    ]
    if final_text:
        return " ".join(final_text)
    interim_text = [
        str(event["text"])
        for event in events
        if event.get("type") == "transcript"
        and isinstance(event.get("text"), str)
        and event["text"].strip()
    ]
    return interim_text[-1] if interim_text else ""


def _score_result(reference: str, hypothesis: str) -> dict[str, Any]:
    score = score_transcript(reference, hypothesis)
    return {
        "reference_words": score.reference_words,
        "edit_count": score.edits,
        "word_error_rate": round(score.word_error_rate, 4),
        "word_accuracy_percent": round(score.word_accuracy_percent, 2),
        "reference_characters": score.reference_characters,
        "character_edit_count": score.character_edits,
        "character_error_rate": round(score.character_error_rate, 4),
    }


def _aggregate_scores(scores: list[tuple[int, int]]) -> dict[str, Any]:
    edits = sum(sample_edits for sample_edits, _ in scores)
    words = sum(sample_words for _, sample_words in scores)
    wer = edits / words if words else 0.0
    return {
        "reference_words": words,
        "edit_count": edits,
        "word_error_rate": round(wer, 4),
        "word_accuracy_percent": round(max(0.0, (1 - wer) * 100), 2),
    }


async def _text_chunks(text: str) -> AsyncIterable[str]:
    yield text


async def _synthesize_wav(synthesizer: SpeechSynthesizer, text: str, language: str | None) -> bytes:
    pcm_parts: list[bytes] = []
    total_bytes = 0
    async for chunk in synthesizer.stream_audio(_text_chunks(text), language=language):
        total_bytes += len(chunk)
        if total_bytes > MAX_SYNTHESIZED_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Synthesized audio exceeds 20 MiB")
        if len(chunk) % 2:
            raise ApplicationError(
                "Text-to-speech returned invalid PCM audio",
                status_code=502,
                code="invalid_tts_audio",
            )
        pcm_parts.append(chunk)
    pcm_audio = b"".join(pcm_parts)
    if not pcm_audio:
        raise ApplicationError(
            "Text-to-speech returned no audio",
            status_code=502,
            code="empty_tts_audio",
        )

    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24_000)
        wav_file.writeframes(pcm_audio)
    return output.getvalue()


def _audio_summary(wav_bytes: bytes) -> dict[str, float | int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        sample_rate = wav_file.getframerate()
        pcm_audio = wav_file.readframes(frame_count)
    samples = [sample[0] for sample in struct.iter_unpack("<h", pcm_audio)]
    rms = (sum(sample * sample for sample in samples) / len(samples)) ** 0.5
    return {
        "sample_rate_hz": sample_rate,
        "channels": 1,
        "duration_seconds": round(frame_count / sample_rate, 3),
        "rms": round(rms, 2),
        "peak": max(abs(sample) for sample in samples),
        "silent_or_very_quiet": rms < 50,
    }


@router.post("/stt")
async def evaluate_stt(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    audio_file: Annotated[UploadFile, File(description="One WAV file")],
    transcript_file: Annotated[
        UploadFile, File(description="One UTF-8 TXT reference with the matching filename stem")
    ],
) -> dict[str, Any]:
    del current_user
    _check_evaluation_enabled(settings)
    transcriber = _get_transcriber(settings)
    stem, audio_bytes, reference = await _load_pair(audio_file, transcript_file)
    hypothesis = await _transcribe_wav(audio_bytes, transcriber=transcriber)
    score = _score_result(reference, hypothesis)
    corpus = _aggregate_scores([(score["edit_count"], score["reference_words"])])
    return {
        "metric": "word_error_rate",
        "corpus": corpus,
        "sample": {
            "sample": stem,
            "reference_transcript": reference,
            "recognized_transcript": hypothesis,
            **score,
        },
    }


@router.post("/tts")
async def evaluate_tts(
    request: Request,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    reference_audio_file: Annotated[
        UploadFile, File(description="One reference WAV file")
    ],
    transcript_file: Annotated[
        UploadFile, File(description="One UTF-8 TXT source text with the matching filename stem")
    ],
    language: Annotated[str | None, Form(max_length=32)] = None,
) -> Response:
    del current_user
    _check_evaluation_enabled(settings)
    transcriber = _get_transcriber(settings)
    synthesizer = _get_speech_synthesizer(request)
    stem, reference_wav, text = await _load_pair(reference_audio_file, transcript_file)

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_STORED) as output_zip:
        reference_transcript = await _transcribe_wav(reference_wav, transcriber=transcriber)
        generated_wav = await _synthesize_wav(synthesizer, text, language)
        generated_transcript = await _transcribe_wav(generated_wav, transcriber=transcriber)
        reference_score = _score_result(text, reference_transcript)
        generated_score = _score_result(text, generated_transcript)
        try:
            generated_vs_reference = _score_result(reference_transcript, generated_transcript)
        except ValueError:
            generated_vs_reference = None

        wav_filename = f"{stem}.generated.wav"
        output_zip.writestr(wav_filename, generated_wav)
        summary = {
            "metric": "word_error_rate",
            "reference_audio": reference_score,
            "generated_audio": generated_score,
            "sample": {
                "sample": stem,
                "reference_text": text,
                "reference_audio_asr": reference_transcript,
                "reference_audio_wer": reference_score["word_error_rate"],
                "generated_audio_asr": generated_transcript,
                "generated_audio_wer": generated_score["word_error_rate"],
                "generated_vs_reference_asr_wer": (
                    generated_vs_reference["word_error_rate"]
                    if generated_vs_reference is not None
                    else None
                ),
                "generated_audio_summary": _audio_summary(generated_wav),
                "wav_file": wav_filename,
            },
            "note": (
                "WER measures recognized content, not naturalness or speaker similarity. "
                "Listen to the included WAV files for pronunciation and prosody."
            ),
        }
        output_zip.writestr(
            "summary.json",
            json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    return Response(
        content=archive.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="tts-evaluation.zip"'},
    )

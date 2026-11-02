"""Audio dataset parsing and transcript scoring for development evaluations."""

import asyncio
import io
import struct
import unicodedata
import wave
from dataclasses import dataclass

from backend.core.stt.transcription import AudioQueue
from backend.core.voice.audio import AudioFrame, AudioStreamEnd

TARGET_SAMPLE_RATE = 16_000
FRAME_DURATION_MS = 20
FRAME_SIZE_BYTES = TARGET_SAMPLE_RATE * FRAME_DURATION_MS * 2 // 1_000
MAX_AUDIO_DURATION_SECONDS = 90
MAX_AUDIO_FILE_BYTES = 20 * 1024 * 1024
MAX_TRANSCRIPT_FILE_BYTES = 8 * 1024
MAX_TOTAL_UPLOAD_BYTES = 50 * 1024 * 1024

_DIGIT_WORDS = {
    "ar": {
        "صفر": "0", "واحد": "1", "واحدة": "1", "اثنان": "2", "اثنين": "2",
        "ثلاثة": "3", "أربعة": "4", "اربعة": "4", "خمسة": "5", "ستة": "6",
        "سبعة": "7", "ثمانية": "8", "تسعة": "9",
    },
    "bn": {
        "শূন্য": "0", "এক": "1", "দুই": "2", "তিন": "3", "চার": "4",
        "পাঁচ": "5", "ছয়": "6", "ছয়": "6", "সাত": "7", "আট": "8", "নয়": "9", "নয়": "9",
    },
    "de": {
        "null": "0", "eins": "1", "zwei": "2", "drei": "3", "vier": "4",
        "fünf": "5", "fuenf": "5", "sechs": "6", "sieben": "7", "acht": "8", "neun": "9",
    },
    "en": {
        "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    },
    "es": {
        "cero": "0", "uno": "1", "una": "1", "dos": "2", "tres": "3", "cuatro": "4",
        "cinco": "5", "seis": "6", "siete": "7", "ocho": "8", "nueve": "9",
    },
    "fr": {
        "zéro": "0", "zero": "0", "un": "1", "une": "1", "deux": "2", "trois": "3",
        "quatre": "4", "cinq": "5", "six": "6", "sept": "7", "huit": "8", "neuf": "9",
    },
    "hi": {
        "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4",
        "पाँच": "5", "पांच": "5", "छह": "6", "छः": "6", "सात": "7", "आठ": "8", "नौ": "9",
    },
    "ta": {
        "பூஜ்ஜியம்": "0", "சுழியம்": "0", "ஒன்று": "1", "இரண்டு": "2", "மூன்று": "3",
        "நான்கு": "4", "ஐந்து": "5", "ஆறு": "6", "ஏழு": "7", "எட்டு": "8", "ஒன்பது": "9",
    },
    "te": {
        "సున్నా": "0", "ఒకటి": "1", "రెండు": "2", "మూడు": "3", "నాలుగు": "4",
        "ఐదు": "5", "ఆరు": "6", "ఏడు": "7", "ఎనిమిది": "8", "తొమ్మిది": "9",
    },
}


@dataclass(frozen=True, slots=True)
class TranscriptScore:
    reference_words: int
    edits: int
    word_error_rate: float
    word_accuracy_percent: float
    reference_characters: int
    character_edits: int
    character_error_rate: float


def decode_wav_to_16khz_mono(wav_bytes: bytes) -> tuple[bytes, float]:
    """Convert uncompressed PCM WAV audio to mono 16-bit 16 kHz PCM."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            if (
                wav_file.getcomptype() != "NONE"
                or not 1 <= channels <= 8
                or sample_width not in {1, 2, 3, 4}
                or not 8_000 <= sample_rate <= 96_000
                or frame_count <= 0
                or frame_count / sample_rate > MAX_AUDIO_DURATION_SECONDS
            ):
                raise ValueError(
                    "WAV must be uncompressed PCM (8, 16, 24, or 32-bit), "
                    "1-8 channels, 8-96 kHz, and no longer than 90 seconds"
                )
            pcm_bytes = wav_file.readframes(frame_count)
    except (wave.Error, EOFError) as error:
        raise ValueError("File is not a valid WAV audio file") from error

    bytes_per_frame = sample_width * channels
    if len(pcm_bytes) != frame_count * bytes_per_frame:
        raise ValueError("WAV audio data is incomplete")

    mono_samples: list[int] = []
    for frame_start in range(0, len(pcm_bytes), bytes_per_frame):
        channel_total = 0
        for channel in range(channels):
            sample_start = frame_start + channel * sample_width
            sample_bytes = pcm_bytes[sample_start : sample_start + sample_width]
            if sample_width == 1:
                sample = (sample_bytes[0] - 128) << 8
            elif sample_width == 2:
                sample = struct.unpack_from("<h", sample_bytes)[0]
            elif sample_width == 3:
                sample = int.from_bytes(sample_bytes, "little", signed=True) >> 8
            else:
                sample = struct.unpack_from("<i", sample_bytes)[0] >> 16
            channel_total += sample
        mono_samples.append(channel_total // channels)

    duration = frame_count / sample_rate
    if sample_rate == TARGET_SAMPLE_RATE:
        resampled = bytearray(frame_count * 2)
        for index, sample in enumerate(mono_samples):
            struct.pack_into("<h", resampled, index * 2, sample)
        return bytes(resampled), duration

    output_count = max(1, round(frame_count * TARGET_SAMPLE_RATE / sample_rate))
    resampled = bytearray(output_count * 2)
    for output_index in range(output_count):
        source_position = output_index * sample_rate / TARGET_SAMPLE_RATE
        left_index = min(int(source_position), frame_count - 1)
        right_index = min(left_index + 1, frame_count - 1)
        fraction = source_position - left_index
        interpolated = round(
            mono_samples[left_index] * (1 - fraction)
            + mono_samples[right_index] * fraction
        )
        struct.pack_into("<h", resampled, output_index * 2, interpolated)
    return bytes(resampled), duration


def audio_frames(pcm_s16le: bytes) -> list[AudioFrame]:
    """Split PCM into the 20 ms frames expected by the streaming STT adapter."""
    frames: list[AudioFrame] = []
    for sequence, start in enumerate(range(0, len(pcm_s16le), FRAME_SIZE_BYTES)):
        chunk = pcm_s16le[start : start + FRAME_SIZE_BYTES]
        if len(chunk) < FRAME_SIZE_BYTES:
            chunk += bytes(FRAME_SIZE_BYTES - len(chunk))
        frames.append(AudioFrame(sequence, chunk, FRAME_DURATION_MS))
    return frames


def score_transcript(
    reference: str,
    hypothesis: str,
    *,
    language: str | None = None,
) -> TranscriptScore:
    """Compute WER/CER with Unicode tokenization and spoken-digit normalization."""
    reference_words = _tokens(reference, language=language)
    hypothesis_words = _tokens(hypothesis, language=language)
    if not reference_words:
        raise ValueError("Reference transcript must contain at least one word")

    previous_row = list(range(len(hypothesis_words) + 1))
    for reference_index, reference_word in enumerate(reference_words, start=1):
        current_row = [reference_index]
        for hypothesis_index, hypothesis_word in enumerate(hypothesis_words, start=1):
            current_row.append(
                min(
                    previous_row[hypothesis_index] + 1,
                    current_row[hypothesis_index - 1] + 1,
                    previous_row[hypothesis_index - 1]
                    + (reference_word != hypothesis_word),
                )
            )
        previous_row = current_row

    edits = previous_row[-1]
    wer = edits / len(reference_words)
    reference_characters = list(
        "".join(reference_words)
    )
    hypothesis_characters = list("".join(hypothesis_words))
    character_edits = _edit_distance(reference_characters, hypothesis_characters)
    return TranscriptScore(
        reference_words=len(reference_words),
        edits=edits,
        word_error_rate=wer,
        word_accuracy_percent=max(0.0, (1 - wer) * 100),
        reference_characters=len(reference_characters),
        character_edits=character_edits,
        character_error_rate=(
            character_edits / len(reference_characters) if reference_characters else 0.0
        ),
    )


def transcript_tokens(text: str) -> list[str]:
    """Return normalized words for comparing ASR output strings."""
    return _tokens(text)


def _tokens(text: str, *, language: str | None = None) -> list[str]:
    normalized = unicodedata.normalize("NFC", text).casefold()
    raw_tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"L", "N"} or category[0] == "M" and current or character == "_":
            current.append(character)
        elif current:
            raw_tokens.append("".join(current))
            current = []
    if current:
        raw_tokens.append("".join(current))

    language_code = language.replace("_", "-").split("-", maxsplit=1)[0].casefold() if language else ""
    digit_words = _DIGIT_WORDS.get(language_code, {})
    tokens: list[str] = []
    for token in raw_tokens:
        digit_word = digit_words.get(token)
        if digit_word is not None:
            tokens.append(digit_word)
        elif token.isdecimal():
            tokens.extend(str(unicodedata.digit(character)) for character in token)
        else:
            tokens.append(token)
    return tokens


def _edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous_row = list(range(len(hypothesis) + 1))
    for reference_index, reference_character in enumerate(reference, start=1):
        current_row = [reference_index]
        for hypothesis_index, hypothesis_character in enumerate(hypothesis, start=1):
            current_row.append(
                min(
                    previous_row[hypothesis_index] + 1,
                    current_row[hypothesis_index - 1] + 1,
                    previous_row[hypothesis_index - 1]
                    + (reference_character != hypothesis_character),
                )
            )
        previous_row = current_row
    return previous_row[-1]


def new_audio_queue() -> AudioQueue:
    """Create a Deepgram queue containing the provided audio and stream terminators."""
    return asyncio.Queue()


def end_audio_queue(queue: AudioQueue, frames: list[AudioFrame]) -> None:
    """Populate an unbounded queue for one size-limited test sample."""
    for frame in frames:
        queue.put_nowait(frame)
    queue.put_nowait(AudioStreamEnd())
    queue.put_nowait(None)

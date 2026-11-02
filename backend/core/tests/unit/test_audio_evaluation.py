"""Tests for audio dataset decoding and transcript evaluation metrics."""

import io
import struct
import unittest
import wave

from fastapi import HTTPException

from backend.core.config import Settings
from backend.core.stt.evaluation import (
    FRAME_SIZE_BYTES,
    audio_frames,
    decode_wav_to_16khz_mono,
    score_transcript,
)
from backend.routes.audio_evaluation import _check_evaluation_enabled


def make_wav(*, sample_rate: int, channels: int, frames: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        samples = bytearray()
        for index in range(frames):
            for channel in range(channels):
                samples.extend(struct.pack("<h", 1000 + index + channel))
        wav_file.writeframes(samples)
    return output.getvalue()


class AudioEvaluationTests(unittest.TestCase):
    def test_word_and_character_scores_for_exact_and_incorrect_transcripts(self) -> None:
        exact = score_transcript("Hello, உலகம்!", "hello உலகம்")
        self.assertEqual(exact.word_error_rate, 0)
        self.assertEqual(exact.character_error_rate, 0)

        incorrect = score_transcript("turn on the lights", "turn off lights now")
        self.assertEqual(incorrect.reference_words, 4)
        self.assertEqual(incorrect.edits, 3)
        self.assertEqual(incorrect.word_error_rate, 0.75)
        self.assertGreater(incorrect.character_error_rate, 0)

    def test_scoring_normalizes_spoken_digit_sequences_for_language(self) -> None:
        for language, words, digits in (
            ("en-IN", "four eight two nine one", "48291"),
            ("de-DE", "vier acht zwei neun eins", "48291"),
            ("hi-IN", "चार आठ दो नौ एक", "48291"),
            ("ta-IN", "நான்கு எட்டு இரண்டு ஒன்பது ஒன்று", "48291"),
            ("te-IN", "నాలుగు ఎనిమిది రెండు తొమ్మిది ఒకటి", "48291"),
        ):
            with self.subTest(language=language):
                result = score_transcript(words, digits, language=language)
                self.assertEqual(result.word_error_rate, 0)
                self.assertEqual(result.character_error_rate, 0)

    def test_unicode_tokenization_preserves_combining_marks(self) -> None:
        result = score_transcript(
            "कृपया बैठक की जानकारी भेजें",
            "कृपया बैठक की जानकारी भेजें",
            language="hi-IN",
        )

        self.assertEqual(result.reference_words, 5)
        self.assertEqual(result.word_error_rate, 0)

    def test_audio_evaluation_is_disabled_by_default_and_can_be_enabled_in_production(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            _check_evaluation_enabled(Settings(_env_file=None))
        self.assertEqual(raised.exception.status_code, 404)

        settings = Settings(
            _env_file=None,
            environment="production",
            enable_audio_evaluation=True,
            frontend_origins=["https://frontend.example.com"],
            secret_key="x" * 32,
        )
        _check_evaluation_enabled(settings)

    def test_wav_decoder_downmixes_and_resamples_stereo_audio(self) -> None:
        wav_bytes = make_wav(sample_rate=8_000, channels=2, frames=800)

        pcm_bytes, duration = decode_wav_to_16khz_mono(wav_bytes)

        self.assertEqual(len(pcm_bytes), 1_600 * 2)
        self.assertAlmostEqual(duration, 0.1)

    def test_wav_decoder_converts_unsigned_8_bit_audio(self) -> None:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(1)
            wav_file.setframerate(16_000)
            wav_file.writeframes(bytes([128, 255]))

        pcm_bytes, _ = decode_wav_to_16khz_mono(output.getvalue())

        self.assertEqual(struct.unpack("<hh", pcm_bytes), (0, 32_512))

    def test_wav_decoder_converts_and_downmixes_24_bit_audio(self) -> None:
        output = io.BytesIO()
        samples = (0x10000, 0x20000, 0x30000)
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(3)
            wav_file.setsampwidth(3)
            wav_file.setframerate(16_000)
            wav_file.writeframes(
                b"".join(sample.to_bytes(3, "little", signed=True) for sample in samples)
            )

        pcm_bytes, _ = decode_wav_to_16khz_mono(output.getvalue())

        self.assertEqual(struct.unpack("<h", pcm_bytes), (512,))

    def test_wav_decoder_rejects_non_wav_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid WAV"):
            decode_wav_to_16khz_mono(b"not wav")

    def test_audio_frames_pad_final_frame_to_protocol_size(self) -> None:
        frames = audio_frames(bytes(FRAME_SIZE_BYTES + 2))

        self.assertEqual(len(frames), 2)
        self.assertTrue(all(len(frame.pcm_s16le) == FRAME_SIZE_BYTES for frame in frames))
        self.assertEqual(frames[1].sequence, 1)


if __name__ == "__main__":
    unittest.main()

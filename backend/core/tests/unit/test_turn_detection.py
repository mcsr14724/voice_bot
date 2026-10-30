"""Unit tests for energy-based audio turn boundaries."""

import unittest

from backend.core.voice.audio import AudioFrame
from backend.core.voice.turn_detection import EnergyTurnDetector


class EnergyTurnDetectorTests(unittest.TestCase):
    def test_requires_consecutive_speech_frames_and_then_silence(self) -> None:
        detector = EnergyTurnDetector(min_speech_frames=2, silence_duration_ms=40)
        speech = AudioFrame(sequence=0, pcm_s16le=bytes([0xE8, 0x03]) * 160, frame_duration_ms=20)
        silence = AudioFrame(sequence=2, pcm_s16le=bytes(320), frame_duration_ms=20)

        self.assertEqual(detector.process(speech), [])
        self.assertEqual(detector.process(AudioFrame(1, speech.pcm_s16le, 20))[0].type, "speech.started")
        self.assertEqual(detector.process(silence), [])
        ended = detector.process(AudioFrame(3, silence.pcm_s16le, 20))
        self.assertEqual([(event.type, event.sequence) for event in ended], [("speech.ended", 3)])

    def test_audio_stop_ends_open_speech_turn(self) -> None:
        detector = EnergyTurnDetector(min_speech_frames=1)
        detector.process(AudioFrame(4, bytes([0xE8, 0x03]) * 160, 20))

        events = detector.end_stream(sequence=4)

        self.assertEqual([(event.type, event.sequence) for event in events], [("speech.ended", 4)])

    def test_rejects_incomplete_pcm_sample(self) -> None:
        detector = EnergyTurnDetector()
        with self.assertRaises(ValueError):
            detector.process(AudioFrame(0, b"\x00", 20))

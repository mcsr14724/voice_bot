"""Tests for Azure Speech voice selection by language."""

import unittest

from backend.core.tts.azure import _voice_for_language


class AzureVoiceSelectionTests(unittest.TestCase):
    def test_selects_a_voice_matching_each_evaluation_language(self) -> None:
        expected = {
            "ar-EG": "ar-EG-SalmaNeural",
            "bn-BD": "bn-IN-TanishaaNeural",
            "de-DE": "de-DE-KatjaNeural",
            "en-IN": "en-IN-NeerjaNeural",
            "es-ES": "es-ES-ElviraNeural",
            "fr-FR": "fr-FR-DeniseNeural",
            "hi-IN": "hi-IN-SwaraNeural",
            "ta-IN": "ta-IN-PallaviNeural",
            "te-IN": "te-IN-ShrutiNeural",
        }

        for language, voice in expected.items():
            with self.subTest(language=language):
                self.assertEqual(_voice_for_language(language, "fallback"), voice)

    def test_preserves_configured_fallback_for_unspecified_or_unknown_language(self) -> None:
        self.assertEqual(_voice_for_language(None, "fallback"), "fallback")
        self.assertEqual(_voice_for_language("xx-XX", "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()

"""Shared response-language policy for the voice model architectures."""

LANGUAGE_RESPONSE_POLICY = (
    "For each latest user turn, distinguish the language the user used from any language "
    "they request for the response. If the user explicitly asks you to answer or speak in a "
    "particular language, respond entirely in that requested language, regardless of the "
    "input language. A request to speak another language is an output-language instruction, "
    "not only a translation request. If no response language is explicitly requested, reply "
    "in the language of the latest user turn, using its normal writing system. Do not let "
    "earlier turns override the latest turn's language choice."
)

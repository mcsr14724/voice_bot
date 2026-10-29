"""Small control-message contract for the authenticated voice WebSocket."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ClientAuthMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["auth"]
    access_token: Annotated[str, Field(min_length=1, max_length=4_096)]


class ClientTextMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text.send"]
    text: Annotated[str, Field(min_length=1, max_length=8_000)]
    voice_response: bool = False


class ClientPing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ping"]
    request_id: Annotated[str, Field(min_length=1, max_length=64)]


class ClientClose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["close"]


class ClientInterrupt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["interrupt"]


class ClientAudioStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["audio.start"]
    encoding: Literal["pcm_s16le"]
    sample_rate: Literal[16000]
    channels: Literal[1]
    frame_duration_ms: Literal[20, 40, 60, 80, 100] = 20


class ClientAudioStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["audio.stop"]


ClientMessage = Annotated[
    ClientTextMessage | ClientPing | ClientClose | ClientInterrupt | ClientAudioStart | ClientAudioStop,
    Field(discriminator="type"),
]

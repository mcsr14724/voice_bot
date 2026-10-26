"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const socketUrl = new URL(API_URL);
socketUrl.protocol = socketUrl.protocol === "https:" ? "wss:" : "ws:";
socketUrl.pathname = `${socketUrl.pathname.replace(/\/$/, "")}/ws/voice`;
const VOICE_SOCKET_URL = socketUrl.toString();
const BACKCHANNELS = new Set([
  "hmm", "hm", "mm", "mmm", "mhm", "uh", "um", "uh huh", "mm hm", "mm hmm", "hmm hmm", "mmhm", "uhuh",
]);

function isBackchannelOnly(text: string): boolean {
  return BACKCHANNELS.has(text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim().replace(/\s+/g, " "));
}

type ServerEvent = {
  type: string;
  session_id?: string;
  text?: string;
  is_final?: boolean;
  speech_final?: boolean;
  code?: string;
  generation?: number;
};
type ChatMessage = { id: string; role: "user" | "assistant"; content: string; interrupted?: boolean };
type ConversationSummary = { id: string; created_at: string; updated_at: string; preview: string };
type ConversationDetail = ConversationSummary & { messages: { role: string; content: string }[] };
type VoiceArchitecture = "cascade" | "gemini_live";

export default function HomePage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [recording, setRecording] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const [voiceArchitecture, setVoiceArchitecture] = useState<VoiceArchitecture>("cascade");
  const [assistantSpeaking, setAssistantSpeaking] = useState(false);
  const [status, setStatus] = useState("Your private space for a conversation.");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [liveTranscript, setLiveTranscript] = useState("");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [deletingConversations, setDeletingConversations] = useState<Set<string>>(new Set());
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const socketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<AudioWorkletNode | null>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);
  const playbackSourcesRef = useRef(new Set<AudioBufferSourceNode>());
  const playbackCursorRef = useRef(0);
  const activeAudioGenerationRef = useRef<number | null>(null);
  const invalidAudioGenerationRef = useRef(0);
  const localBargeTimerRef = useRef<number | null>(null);
  const localPlaybackHeldRef = useRef(false);
  const pendingTextRef = useRef<{ text: string; voice_response: boolean } | null>(null);
  const pendingTranscriptRef = useRef<string[]>([]);
  const currentAssistantIdRef = useRef<string | null>(null);
  const voiceStartPendingRef = useRef(false);
  const voiceModeRef = useRef(false);
  const recordingRef = useRef(false);
  const playbackSuppressedRef = useRef(false);
  const assistantOutputActiveRef = useRef(false);
  const endOfMessagesRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, liveTranscript]);

  useEffect(() => () => {
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    contextRef.current?.close().catch(() => undefined);
    playbackSourcesRef.current.forEach((source) => {
      try { source.stop(); } catch { /* Already stopped. */ }
    });
    playbackContextRef.current?.close().catch(() => undefined);
    socketRef.current?.close(1000, "Page closed");
  }, []);

  const stopAudioPlayback = () => {
    playbackSourcesRef.current.forEach((source) => {
      try { source.stop(); } catch { /* Already stopped. */ }
    });
    playbackSourcesRef.current.clear();
    playbackCursorRef.current = playbackContextRef.current?.currentTime ?? 0;
    setAssistantSpeaking(false);
  };

  const holdPlaybackForSpeechCandidate = () => {
    if (!assistantOutputActiveRef.current || playbackSuppressedRef.current) return;
    if (localBargeTimerRef.current !== null) window.clearTimeout(localBargeTimerRef.current);
    localBargeTimerRef.current = null;
    const context = playbackContextRef.current;
    if (!context) return;
    localPlaybackHeldRef.current = true;
    void context.suspend().catch(() => undefined);
    // A noise/echo VAD event can fail to produce a matching end event. Never
    // leave the output context paused indefinitely while Deepgram decides.
    localBargeTimerRef.current = window.setTimeout(() => {
      localBargeTimerRef.current = null;
      localPlaybackHeldRef.current = false;
      if (!playbackSuppressedRef.current && assistantOutputActiveRef.current) {
        void context.resume().catch(() => undefined);
      }
    }, 1_500);
  };

  const releasePlaybackAfterSpeechCandidate = () => {
    if (!localPlaybackHeldRef.current) return;
    if (localBargeTimerRef.current !== null) window.clearTimeout(localBargeTimerRef.current);
    const context = playbackContextRef.current;
    if (!context) return;
    // Give Deepgram a short grace period to deliver the final/interim transcript.
    localBargeTimerRef.current = window.setTimeout(() => {
      localBargeTimerRef.current = null;
      localPlaybackHeldRef.current = false;
      if (!playbackSuppressedRef.current && assistantOutputActiveRef.current) {
        void context.resume().catch(() => undefined);
      }
    }, 350);
  };

  const confirmBargeIn = (generation?: number) => {
    if (localBargeTimerRef.current !== null) {
      window.clearTimeout(localBargeTimerRef.current);
      localBargeTimerRef.current = null;
    }
    localPlaybackHeldRef.current = false;
    if (typeof generation === "number") {
      invalidAudioGenerationRef.current = Math.max(invalidAudioGenerationRef.current, generation);
    }
    activeAudioGenerationRef.current = null;
    assistantOutputActiveRef.current = false;
    playbackSuppressedRef.current = true;
    stopAudioPlayback();
  };

  const closePlaybackContext = () => {
    if (localBargeTimerRef.current !== null) {
      window.clearTimeout(localBargeTimerRef.current);
      localBargeTimerRef.current = null;
    }
    localPlaybackHeldRef.current = false;
    activeAudioGenerationRef.current = null;
    invalidAudioGenerationRef.current = 0;
    assistantOutputActiveRef.current = false;
    stopAudioPlayback();
    playbackContextRef.current?.close().catch(() => undefined);
    playbackContextRef.current = null;
  };

  const playPcmChunk = (data: ArrayBuffer) => {
    const sampleCount = Math.floor(data.byteLength / 2);
    const context = playbackContextRef.current;
    if (!sampleCount || !context) return;
    if (context.state === "suspended" && !localPlaybackHeldRef.current) {
      void context.resume();
    }
    const samples = new Float32Array(sampleCount);
    const view = new DataView(data);
    for (let index = 0; index < sampleCount; index += 1) {
      samples[index] = view.getInt16(index * 2, true) / 32768;
    }
    const buffer = context.createBuffer(1, sampleCount, 24_000);
    buffer.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.02, playbackCursorRef.current);
    source.start(startAt);
    playbackCursorRef.current = startAt + buffer.duration;
    playbackSourcesRef.current.add(source);
    source.onended = () => playbackSourcesRef.current.delete(source);
    setAssistantSpeaking(true);
  };

  const stopMicrophone = () => {
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    contextRef.current?.close().catch(() => undefined);
    processorRef.current = null;
    sourceRef.current = null;
    streamRef.current = null;
    contextRef.current = null;
    setRecording(false);
    recordingRef.current = false;
  };

  async function startMicrophone(socket: WebSocket) {
    if (recording || socket.readyState !== WebSocket.OPEN) return;
    try {
      if (!playbackContextRef.current) playbackContextRef.current = new AudioContext();
      if (playbackContextRef.current.state === "suspended") await playbackContextRef.current.resume();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const context = new AudioContext();
      await context.audioWorklet.addModule("/audio/pcm-capture-processor.js");
      const source = context.createMediaStreamSource(stream);
      const processor = new AudioWorkletNode(context, "pcm-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      processor.port.onmessage = (event: MessageEvent<{
        buffer: ArrayBuffer;
        speechStarted: boolean;
        speechEnded: boolean;
      }>) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(event.data.buffer);
        if (event.data.speechStarted) holdPlaybackForSpeechCandidate();
        if (event.data.speechEnded) releasePlaybackAfterSpeechCandidate();
      };
      socket.send(JSON.stringify({
        type: "audio.start",
        encoding: "pcm_s16le",
        sample_rate: 16_000,
        channels: 1,
        frame_duration_ms: 20,
      }));
      source.connect(processor);
      processor.connect(context.destination);
      streamRef.current = stream;
      contextRef.current = context;
      sourceRef.current = source;
      processorRef.current = processor;
      setRecording(true);
      recordingRef.current = true;
      setVoiceMode(true);
      voiceModeRef.current = true;
      setStatus("Listening. Speak naturally; you can interrupt Vox at any time.");
      setError("");
    } catch (cause) {
      setVoiceMode(false);
      voiceModeRef.current = false;
      setStatus(cause instanceof Error ? cause.message : "Microphone access failed.");
    }
  }

  const loadConversations = async (accessToken: string) => {
    setLoadingHistory(true);
    try {
      const response = await fetch(`${API_URL}/conversations`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!response.ok) throw new Error(await responseMessage(response));
      setConversations((await response.json()) as ConversationSummary[]);
    } catch {
      setError("Could not load your saved conversations.");
    } finally {
      setLoadingHistory(false);
    }
  };

  useEffect(() => {
    if (!token) {
      setConversations([]);
      return;
    }
    void loadConversations(token);
  }, [token]);

  const appendUserMessage = (text: string) => {
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content: text }]);
  };

  const appendAssistantDelta = (text: string) => {
    let assistantId = currentAssistantIdRef.current;
    if (!assistantId) {
      assistantId = crypto.randomUUID();
      currentAssistantIdRef.current = assistantId;
      setMessages((current) => [...current, { id: assistantId!, role: "assistant", content: text }]);
      return;
    }
    setMessages((current) => current.map((message) =>
      message.id === assistantId ? { ...message, content: message.content + text } : message,
    ));
  };

  const commitVoiceTranscript = () => {
    const text = pendingTranscriptRef.current.join(" ").trim();
    pendingTranscriptRef.current = [];
    setLiveTranscript("");
    if (text) appendUserMessage(text);
  };

  const connect = (
    conversationId?: string,
    startVoice = false,
    architecture: VoiceArchitecture = voiceArchitecture,
  ) => {
    if (!token) return;
    const existing = socketRef.current;
    if (existing) {
      if (startVoice) {
        voiceStartPendingRef.current = true;
        if (existing.readyState === WebSocket.OPEN) {
          voiceStartPendingRef.current = false;
          void startMicrophone(existing);
        }
      }
      return;
    }
    setStatus("Connecting to your private conversation…");
    const params = new URLSearchParams({ architecture });
    if (conversationId) params.set("conversation_id", conversationId);
    const query = `?${params.toString()}`;
    const socket = new WebSocket(`${VOICE_SOCKET_URL}${query}`);
    socket.binaryType = "arraybuffer";
    socketRef.current = socket;
    if (startVoice) voiceStartPendingRef.current = true;

    socket.onopen = () => {
      setConnected(true);
      setStatus(startVoice ? "Starting voice mode…" : "Ready when you are.");
      if (pendingTextRef.current) {
        socket.send(JSON.stringify({ type: "text.send", ...pendingTextRef.current }));
        pendingTextRef.current = null;
      }
      if (voiceStartPendingRef.current) {
        voiceStartPendingRef.current = false;
        void startMicrophone(socket);
      }
    };
    socket.onmessage = (event: MessageEvent) => {
      if (socketRef.current !== socket) return;
      if (event.data instanceof ArrayBuffer) {
        if (architecture === "gemini_live") {
          if (!playbackSuppressedRef.current) playPcmChunk(event.data);
        } else if (event.data.byteLength > 4) {
          const generation = new DataView(event.data, 0, 4).getUint32(0, false);
          if (
            !playbackSuppressedRef.current &&
            generation === activeAudioGenerationRef.current &&
            generation > invalidAudioGenerationRef.current
          ) {
            playPcmChunk(event.data.slice(4));
          }
        }
        return;
      }
      try {
        const message = JSON.parse(event.data as string) as ServerEvent;
        if (message.type === "session.ready") {
          activeAudioGenerationRef.current = null;
          invalidAudioGenerationRef.current = 0;
          if (message.session_id) setActiveConversationId(message.session_id);
        }
        if (message.type === "transcript") {
          const text = message.text?.trim() ?? "";
          if (message.speech_final && isBackchannelOnly([...pendingTranscriptRef.current, text].join(" "))) {
            pendingTranscriptRef.current = [];
            setLiveTranscript("");
            releasePlaybackAfterSpeechCandidate();
            return;
          }
          if ((message.is_final || message.speech_final) && text) {
            pendingTranscriptRef.current = [...pendingTranscriptRef.current, text];
            setLiveTranscript(pendingTranscriptRef.current.join(" "));
            if (message.speech_final) commitVoiceTranscript();
          } else if (text) {
            setLiveTranscript([...pendingTranscriptRef.current, text].join(" "));
          }
          if (message.speech_final) setStatus("Thinking of a reply…");
          else if (recordingRef.current) setStatus("Listening…");
        }
        if (message.type === "speech.started") holdPlaybackForSpeechCandidate();
        if (message.type === "speech.ended") {
          releasePlaybackAfterSpeechCandidate();
          if (pendingTranscriptRef.current.length) {
            commitVoiceTranscript();
            setStatus("Thinking of a reply…");
          }
        }
        if (message.type === "speech.backchannel") {
          pendingTranscriptRef.current = [];
          setLiveTranscript("");
          releasePlaybackAfterSpeechCandidate();
          setStatus(assistantOutputActiveRef.current ? "Vox is speaking. Talk any time to interrupt." : "Listening. Speak naturally.");
        }
        if (message.type === "assistant.delta" && message.text) {
          appendAssistantDelta(message.text);
          setStatus(voiceModeRef.current ? "Vox is speaking. Talk any time to interrupt." : "Vox is replying…");
        }
        if (message.type === "assistant.audio.started") {
          if (
            architecture === "cascade" &&
            (typeof message.generation !== "number" || message.generation <= invalidAudioGenerationRef.current)
          ) return;
          activeAudioGenerationRef.current = typeof message.generation === "number" ? message.generation : null;
          assistantOutputActiveRef.current = true;
          playbackSuppressedRef.current = false;
          const context = playbackContextRef.current;
          if (context?.state === "suspended") void context.resume().catch(() => undefined);
          setStatus("Preparing a spoken reply…");
        }
        if (message.type === "assistant.audio.ended") {
          if (typeof message.generation === "number" && message.generation !== activeAudioGenerationRef.current) return;
          activeAudioGenerationRef.current = null;
          assistantOutputActiveRef.current = false;
          setAssistantSpeaking(false);
          setStatus(recordingRef.current ? "Listening. Speak naturally." : "Reply complete.");
        }
        if (message.type === "assistant.completed") currentAssistantIdRef.current = null;
        if (message.type === "conversation.turn_saved") {
          currentAssistantIdRef.current = null;
          if (token) void loadConversations(token);
        }
        if (message.type === "response.interrupted") {
          confirmBargeIn(message.generation);
          const assistantId = currentAssistantIdRef.current;
          if (assistantId) {
            setMessages((current) => current.map((item) =>
              item.id === assistantId ? { ...item, interrupted: true } : item,
            ));
            currentAssistantIdRef.current = null;
          }
          setStatus("You’re speaking. Vox is listening.");
        }
        if (message.type === "assistant.error") {
          currentAssistantIdRef.current = null;
          setStatus("Vox couldn’t complete that reply. Try speaking again.");
        }
        if (message.type === "assistant.unavailable") setStatus("Add a Gemini key to enable replies.");
        if (message.type === "assistant.audio.error") {
          if (typeof message.generation === "number" && message.generation !== activeAudioGenerationRef.current) return;
          activeAudioGenerationRef.current = null;
          assistantOutputActiveRef.current = false;
          setStatus("The text reply arrived, but voice playback failed.");
        }
        if (message.type === "assistant.audio.unavailable") setStatus("Speech output is not configured.");
        if (message.type === "transcription.error") setStatus("Speech recognition had a problem. I’m still listening.");
        if (message.type === "transcription.unavailable") setStatus("Speech recognition is not configured.");
        if (message.type === "conversation.error") setStatus("Could not start this reply. Please try again.");
      } catch {
        setError("Received an unreadable server message.");
      }
    };
    socket.onerror = () => setError("Could not connect. Check the backend connection.");
    socket.onclose = () => {
      socketRef.current = null;
      voiceStartPendingRef.current = false;
      stopMicrophone();
      closePlaybackContext();
      setConnected(false);
      setAssistantSpeaking(false);
      if (voiceModeRef.current) {
        voiceModeRef.current = false;
        setVoiceMode(false);
      }
    };
  };

  const disconnect = () => {
    const socket = socketRef.current;
    if (recording && socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "audio.stop" }));
    }
    stopMicrophone();
    closePlaybackContext();
    socket?.close(1000, "Conversation ended");
    socketRef.current = null;
    setConnected(false);
    setVoiceMode(false);
    voiceModeRef.current = false;
    setAssistantSpeaking(false);
  };

  const toggleVoiceMode = () => {
    setError("");
    if (voiceMode || voiceStartPendingRef.current) {
      voiceStartPendingRef.current = false;
      const socket = socketRef.current;
      if (recording && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "audio.stop" }));
      }
      stopMicrophone();
      setVoiceMode(false);
      voiceModeRef.current = false;
      setStatus("Voice mode is off. You can keep chatting here.");
      return;
    }
    setVoiceMode(true);
    voiceModeRef.current = true;
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      void startMicrophone(socketRef.current);
    } else {
      connect(activeConversationId ?? undefined, true);
    }
  };

  const submitText = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !token) return;
    appendUserMessage(text);
    setDraft("");
    setStatus("Thinking of a reply…");
    const payload = { text, voice_response: voiceMode };
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: "text.send", ...payload }));
    } else {
      pendingTextRef.current = payload;
      connect(activeConversationId ?? undefined);
    }
  };

  const handleComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  const startNewChat = () => {
    const keepVoice = voiceMode;
    disconnect();
    setMessages([]);
    setLiveTranscript("");
    pendingTranscriptRef.current = [];
    currentAssistantIdRef.current = null;
    setActiveConversationId(null);
    setStatus(keepVoice ? "Starting a new voice conversation…" : "Start a new conversation.");
    if (keepVoice) window.setTimeout(() => connect(undefined, true), 100);
  };

  const openConversation = async (conversationId: string) => {
    if (!token) return;
    if (socketRef.current) disconnect();
    setError("");
    try {
      const response = await fetch(`${API_URL}/conversations/${encodeURIComponent(conversationId)}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error(await responseMessage(response));
      const detail = (await response.json()) as ConversationDetail;
      setActiveConversationId(detail.id);
      setMessages(detail.messages.map((message, index) => ({
        id: `${detail.id}-${index}`,
        role: message.role === "assistant" ? "assistant" : "user",
        content: message.content,
      })));
      setStatus("Conversation loaded. Continue by typing or turning on voice mode.");
    } catch {
      setError("Could not open that conversation.");
    }
  };

  const deleteConversation = async (conversation: ConversationSummary) => {
    if (!token || deletingConversations.has(conversation.id)) return;
    const title = conversation.preview || "Voice conversation";
    if (!window.confirm(`Delete “${title}”? This removes the chat and its saved transcript.`)) return;

    const deletingActiveConversation = activeConversationId === conversation.id;
    if (deletingActiveConversation && socketRef.current) disconnect();
    setError("");
    setDeletingConversations((current) => new Set(current).add(conversation.id));
    try {
      const response = await fetch(`${API_URL}/conversations/${encodeURIComponent(conversation.id)}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error(await responseMessage(response));
      setConversations((current) => current.filter((item) => item.id !== conversation.id));
      if (deletingActiveConversation) {
        setMessages([]);
        setLiveTranscript("");
        pendingTranscriptRef.current = [];
        currentAssistantIdRef.current = null;
        setActiveConversationId(null);
        setStatus("Chat deleted. Start a new conversation.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete this chat.");
    } finally {
      setDeletingConversations((current) => {
        const next = new Set(current);
        next.delete(conversation.id);
        return next;
      });
    }
  };

  const submitCredentials = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setStatus(mode === "register" ? "Creating your account…" : "Signing in…");
    try {
      if (mode === "register") {
        const registration = await fetch(`${API_URL}/auth/register`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ email, password }),
        });
        if (!registration.ok) throw new Error(await responseMessage(registration));
      }
      const response = await fetch(`${API_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) throw new Error(await responseMessage(response));
      const body = (await response.json()) as { access_token: string };
      setToken(body.access_token);
      setPassword("");
      setStatus("Signed in. Start a chat or turn on voice mode.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not sign in.");
      setStatus("Please check your details and try again.");
    }
  };

  const logout = () => {
    disconnect();
    setToken(null);
    setMessages([]);
    setActiveConversationId(null);
    setPassword("");
    setStatus("Signed out.");
  };

  if (!token) {
    return (
      <main className="auth-screen">
        <a className="brand auth-brand" href="#home" aria-label="Vox home"><span className="brand-mark">v</span> vox<span className="brand-period">.</span></a>
        <section className="auth-card">
          <div className="auth-orb"><span /><span /><span /></div>
          <p className="eyebrow">YOUR CONVERSATION SPACE</p>
          <h1>{mode === "login" ? "Welcome back." : "Make room for a new voice."}</h1>
          <p className="auth-description">Chat by text or switch on voice whenever you’re ready.</p>
          <form className="auth-form" onSubmit={submitCredentials}>
            <label htmlFor="email">Email address</label>
            <input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" />
            <div className="label-row"><label htmlFor="password">Password</label>{mode === "register" && <span>12 characters minimum</span>}</div>
            <input id="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={mode === "register" ? 12 : 1} required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Your password" />
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="auth-submit" type="submit">{mode === "login" ? "Sign in" : "Create account"}<span>↗</span></button>
          </form>
          <p className="auth-switch">{mode === "login" ? "New to Vox?" : "Already have an account?"}<button onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }} type="button">{mode === "login" ? "Create an account" : "Sign in"}</button></p>
          <p className="auth-status" role="status">{status}</p>
        </section>
        <p className="auth-foot">PRIVATE BY DESIGN <span>·</span> YOUR VOICE STAYS YOURS</p>
      </main>
    );
  }

  return (
    <main className="chat-app">
      <aside className={`sidebar ${sidebarOpen ? "" : "sidebar-collapsed"}`}>
        <div className="sidebar-brand-row">
          <a className="brand" href="#home" aria-label="Vox home"><span className="brand-mark">v</span> vox<span className="brand-period">.</span></a>
          <button className="icon-button sidebar-close" onClick={() => setSidebarOpen(false)} aria-label="Close sidebar" title="Close sidebar">‹</button>
        </div>
        <button className="new-chat-button" onClick={startNewChat}><span>＋</span> New chat</button>
        <div className="history-label">RECENT</div>
        <div className="sidebar-history">
          {loadingHistory && <p className="history-note">Loading chats…</p>}
          {!loadingHistory && conversations.length === 0 && <p className="history-note">Your conversations will show up here.</p>}
          {conversations.map((conversation) => (
            <div key={conversation.id} className={`history-item ${activeConversationId === conversation.id ? "selected" : ""}`}>
              <button className="history-button" onClick={() => void openConversation(conversation.id)} title={conversation.preview || "Voice conversation"}>
                <span className="history-glyph">◌</span><span>{conversation.preview || "Voice conversation"}</span>
              </button>
              <button
                className="history-delete"
                type="button"
                aria-label={`Delete ${conversation.preview || "conversation"}`}
                title="Delete chat"
                disabled={deletingConversations.has(conversation.id)}
                onClick={() => void deleteConversation(conversation)}
              >
                {deletingConversations.has(conversation.id) ? "…" : "×"}
              </button>
            </div>
          ))}
        </div>
        <div className="sidebar-bottom"><div className="profile-avatar">{email.slice(0, 1).toUpperCase()}</div><div className="profile-info"><span>{email}</span><small>Personal workspace</small></div><button className="icon-button logout-button" onClick={logout} aria-label="Sign out" title="Sign out">↗</button></div>
      </aside>

      <section className="conversation-panel">
        <header className="chat-header">
          <div className="mobile-brand"><button className="icon-button mobile-menu" onClick={() => setSidebarOpen((open) => !open)} aria-label="Toggle chat history">☰</button><span className="brand-mark">v</span> vox<span className="brand-period">.</span></div>
          {!sidebarOpen && <button className="icon-button sidebar-open-button" onClick={() => setSidebarOpen(true)} aria-label="Show chat history">☰</button>}
          <div className="conversation-title"><span className={`status-dot ${recording ? "live" : connected ? "ready" : ""}`} />{recording ? "Listening" : connected ? "Conversation ready" : "New conversation"}</div>
          <label className="architecture-picker">
            <span>Voice engine</span>
            <select
              value={voiceArchitecture}
              aria-label="Select voice architecture"
              onChange={(event) => {
                const next = event.target.value as VoiceArchitecture;
                if (next === voiceArchitecture) return;
                const resumeVoice = voiceMode || voiceStartPendingRef.current;
                // A session id is not persisted until its first completed turn.
                // Reuse it only when there is conversation content to restore.
                const conversationId = messages.length > 0 ? activeConversationId ?? undefined : undefined;
                if (socketRef.current) disconnect();
                setVoiceArchitecture(next);
                setStatus(next === "gemini_live" ? "Gemini Live will handle listening and speaking." : "Vox will use speech recognition, Gemini, and Azure speech.");
                if (resumeVoice) {
                  window.setTimeout(() => connect(conversationId, true, next), 100);
                }
              }}
            >
              <option value="cascade">STT + Gemini + Azure</option>
              <option value="gemini_live">Gemini Live</option>
            </select>
          </label>
          <button className={`voice-toggle ${voiceMode ? "voice-enabled" : ""}`} onClick={toggleVoiceMode} aria-pressed={voiceMode}>
            <span className="mic-icon" aria-hidden="true">{voiceMode ? "◖" : "♩"}</span>{voiceMode ? "Voice on" : "Voice mode"}
          </button>
        </header>

        <div className="message-scroll" aria-live="polite">
          <div className="message-column">
            {messages.length === 0 && !liveTranscript ? (
              <div className="empty-chat">
                <div className={`hero-orb ${assistantSpeaking ? "orb-speaking" : ""}`}><span /><span /><span /><span /></div>
                <p className="eyebrow">A LITTLE MORE HUMAN</p>
                <h1>What’s on your mind?</h1>
                <p>Write a message below, or turn on voice mode and just start talking.</p>
                <div className="suggestion-row"><span>Ask a question</span><span>Think something through</span><span>Learn something new</span></div>
              </div>
            ) : (
              <div className="message-list">
                {messages.map((message) => (
                  <article key={message.id} className={`message-row ${message.role === "user" ? "user-row" : "assistant-row"}`}>
                    {message.role === "assistant" && <div className="assistant-avatar"><span>v</span></div>}
                    <div className={`message-bubble ${message.role === "user" ? "user-bubble" : "assistant-bubble"}`}>
                      {message.role === "assistant" && <div className="message-author">Vox</div>}
                      <p>{message.content}</p>
                      {message.interrupted && <small className="interrupted-note">Interrupted · ready to listen</small>}
                    </div>
                  </article>
                ))}
                {liveTranscript && <article className="message-row user-row live-row"><div className="message-bubble user-bubble live-bubble"><p>{liveTranscript}</p><small>Listening…</small></div></article>}
                {connected && !assistantSpeaking && status.includes("Thinking") && <div className="thinking-row"><div className="assistant-avatar"><span>v</span></div><div className="thinking-dots"><i /><i /><i /></div></div>}
                <div ref={endOfMessagesRef} />
              </div>
            )}
          </div>
        </div>

        <div className="composer-area">
          {error && <p className="chat-error" role="alert">{error}</p>}
          <div className={`voice-status ${recording ? "voice-status-live" : ""}`} role="status">
            <span className="status-pulse" />{assistantSpeaking ? "Vox is speaking · talk to interrupt" : status}
          </div>
          <form className="composer" onSubmit={submitText}>
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={handleComposerKey} placeholder={voiceMode ? "Say something or type a message…" : "Message Vox…"} rows={1} aria-label="Message Vox" />
            <div className="composer-actions">
              <button className={`composer-voice ${voiceMode ? "composer-voice-active" : ""}`} type="button" onClick={toggleVoiceMode} aria-label={voiceMode ? "Turn voice mode off" : "Turn voice mode on"} title={voiceMode ? "Voice mode on — click to turn off" : "Turn on voice mode"}>
                <span aria-hidden="true">{voiceMode ? "◖" : "♩"}</span>
              </button>
              <button className="send-button" type="submit" disabled={!draft.trim()} aria-label="Send message" title="Send message">↑</button>
            </div>
          </form>
          <p className="composer-hint">{voiceMode ? "Vox listens continuously. Pause to get a reply, or speak over it to jump in." : "Enter to send · Shift + Enter for a new line"}</p>
        </div>
      </section>
    </main>
  );
}

async function responseMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      detail?: string;
      message?: string;
      error?: { message?: string };
    };
    return body.detail ?? body.message ?? body.error?.message ?? `Request failed (${response.status}).`;
  } catch {
    return `Request failed (${response.status}).`;
  }
}

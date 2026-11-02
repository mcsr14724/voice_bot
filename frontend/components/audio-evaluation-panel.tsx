"use client";

import { ChangeEvent, useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const LANGUAGE_TAGS: Record<string, string> = {
  bn: "bn-BD",
  de: "de-DE",
  en: "en-US",
  es: "es-ES",
  fr: "fr-FR",
  hi: "hi-IN",
  ta: "ta-IN",
  te: "te-IN",
};

type AudioPair = {
  key: string;
  name: string;
  language: string | null;
  wav: File;
  transcript: File;
  referenceText: string;
};

type SttResult = {
  recognized_transcript: string;
  word_error_rate: number;
  word_accuracy_percent: number;
  character_error_rate: number;
};

type TtsResult = {
  generated_audio_wer: number;
  reference_audio_wer: number;
  generated_vs_reference_asr_wer: number | null;
  generated_audio_asr: string;
  audioUrl: string;
};

type PairResult = { stt?: SttResult; tts?: TtsResult; error?: string };
type EvaluationMode = "stt" | "tts";

function relativePath(file: File): string {
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
}

function languageFromPath(path: string): string | null {
  const parts = path.split("/");
  const audioIndex = parts.findIndex((part) => part.toLowerCase() === "audio");
  const code = audioIndex >= 0 ? parts[audioIndex + 1] : parts.at(-2);
  return code ? LANGUAGE_TAGS[code.toLowerCase()] ?? null : null;
}

function pairFiles(files: File[]): { pairs: AudioPair[]; unmatched: number } {
  const audio = new Map<string, File>();
  const transcripts = new Map<string, File>();
  for (const file of files) {
    const path = relativePath(file);
    if (/\.wav$/i.test(file.name)) audio.set(path.replace(/\.wav$/i, "").toLowerCase(), file);
    if (/\.txt$/i.test(file.name)) transcripts.set(path.replace(/\.txt$/i, "").toLowerCase(), file);
  }

  const keys = new Set([...audio.keys(), ...transcripts.keys()]);
  const pairs: AudioPair[] = [];
  let unmatched = 0;
  for (const key of keys) {
    const wav = audio.get(key);
    const transcript = transcripts.get(key);
    if (!wav || !transcript) {
      unmatched += 1;
      continue;
    }
    const path = relativePath(wav);
    const name = path.split("/").at(-1)?.replace(/\.wav$/i, "") ?? wav.name;
    pairs.push({
      key,
      name,
      language: languageFromPath(path),
      wav,
      transcript,
      referenceText: "",
    });
  }
  return { pairs: pairs.sort((left, right) => left.key.localeCompare(right.key)), unmatched };
}

async function responseMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string; message?: string };
    return body.detail ?? body.message ?? `Request failed (${response.status}).`;
  } catch {
    return `Request failed (${response.status}).`;
  }
}

function extractStoredZip(buffer: ArrayBuffer): Map<string, Uint8Array> {
  const view = new DataView(buffer);
  const files = new Map<string, Uint8Array>();
  const decoder = new TextDecoder();
  let offset = 0;
  while (offset + 30 <= view.byteLength && view.getUint32(offset, true) === 0x04034b50) {
    const method = view.getUint16(offset + 8, true);
    if (method !== 0) throw new Error("The TTS result used an unsupported ZIP compression method.");
    const size = view.getUint32(offset + 18, true);
    const nameLength = view.getUint16(offset + 26, true);
    const extraLength = view.getUint16(offset + 28, true);
    const nameStart = offset + 30;
    const contentStart = nameStart + nameLength + extraLength;
    const name = decoder.decode(new Uint8Array(buffer, nameStart, nameLength));
    if (contentStart + size > view.byteLength) throw new Error("The TTS result archive is incomplete.");
    files.set(name, new Uint8Array(buffer.slice(contentStart, contentStart + size)));
    offset = contentStart + size;
  }
  return files;
}

export default function AudioEvaluationPanel({ token }: { token: string }) {
  const [pairs, setPairs] = useState<AudioPair[]>([]);
  const [unmatchedCount, setUnmatchedCount] = useState(0);
  const [results, setResults] = useState<Record<string, PairResult>>({});
  const [running, setRunning] = useState<EvaluationMode | null>(null);
  const [progress, setProgress] = useState({ done: 0, total: 0, current: "" });
  const [error, setError] = useState("");
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const objectUrlsRef = useRef<string[]>([]);

  useEffect(() => () => {
    abortControllerRef.current?.abort();
    objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
  }, []);

  useEffect(() => {
    directoryInputRef.current?.setAttribute("webkitdirectory", "");
    directoryInputRef.current?.setAttribute("directory", "");
  }, []);

  const selectFolder = async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const files = input.files;
    if (!files) return;
    const selectedFiles = Array.from(files);
    input.value = "";
    objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    objectUrlsRef.current = [];
    const paired = pairFiles(selectedFiles);
    setPairs(await Promise.all(paired.pairs.map(async (pair) => ({
      ...pair,
      referenceText: (await pair.transcript.text()).trim(),
    }))));
    setUnmatchedCount(paired.unmatched);
    setResults({});
    setError(paired.pairs.length ? "" : "No matching WAV and TXT files were found in that folder.");
  };

  const runEvaluation = async (mode: EvaluationMode) => {
    if (pairs.length === 0 || running) return;
    const controller = new AbortController();
    abortControllerRef.current = controller;
    setRunning(mode);
    setError("");
    setProgress({ done: 0, total: pairs.length, current: "" });
    for (const [index, pair] of pairs.entries()) {
      if (controller.signal.aborted) break;
      setProgress({ done: index, total: pairs.length, current: pair.name });
      setResults((current) => ({
        ...current,
        [pair.key]: {
          ...current[pair.key],
          [mode]: undefined,
          error: undefined,
        },
      }));
      try {
        const form = new FormData();
        form.append(mode === "stt" ? "audio_file" : "reference_audio_file", pair.wav);
        form.append("transcript_file", pair.transcript);
        if (mode === "tts" && pair.language) form.append("language", pair.language);
        const response = await fetch(`${API_URL}/audio-evaluation/${mode}`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: form,
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(await responseMessage(response));

        if (mode === "stt") {
          const body = (await response.json()) as { sample: SttResult };
          setResults((current) => ({
            ...current,
            [pair.key]: { ...current[pair.key], stt: body.sample, error: undefined },
          }));
        } else {
          const entries = extractStoredZip(await response.arrayBuffer());
          const summaryBytes = entries.get("summary.json");
          if (!summaryBytes) throw new Error("The TTS response did not contain summary.json.");
          const summary = JSON.parse(new TextDecoder().decode(summaryBytes)) as {
            sample: {
              generated_audio_wer: number;
              reference_audio_wer: number;
              generated_vs_reference_asr_wer: number | null;
              generated_audio_asr: string;
              wav_file: string;
            };
          };
          const wavBytes = entries.get(summary.sample.wav_file);
          if (!wavBytes) throw new Error("The TTS response did not contain its generated WAV file.");
          const wavCopy = new Uint8Array(wavBytes.byteLength);
          wavCopy.set(wavBytes);
          const audioUrl = URL.createObjectURL(new Blob([wavCopy.buffer], { type: "audio/wav" }));
          objectUrlsRef.current.push(audioUrl);
          setResults((current) => ({
            ...current,
            [pair.key]: {
              ...current[pair.key],
              tts: { ...summary.sample, audioUrl },
              error: undefined,
            },
          }));
        }
      } catch (cause) {
        if (controller.signal.aborted) break;
        const message = cause instanceof Error ? cause.message : "Evaluation failed.";
        if (message === "Audio evaluation is not enabled") {
          setError(`${message}. Enable VOICE_BOT_ENABLE_AUDIO_EVALUATION and restart the development backend.`);
          break;
        }
        setResults((current) => ({
          ...current,
          [pair.key]: { ...current[pair.key], error: message },
        }));
      }
      setProgress({ done: index + 1, total: pairs.length, current: pair.name });
    }
    abortControllerRef.current = null;
    setRunning(null);
  };

  const cancelEvaluation = () => abortControllerRef.current?.abort();

  return (
    <section className="evaluation-panel">
      <header className="evaluation-header">
        <div>
          <p className="eyebrow">LOCAL DATASET TESTING</p>
          <h1>Speech evaluation</h1>
          <p>Upload a dataset folder containing matching WAV audio and TXT transcripts.</p>
        </div>
        <button
          className="evaluation-select-button"
          type="button"
          onClick={() => directoryInputRef.current?.click()}
          disabled={running !== null}
        >
          Choose folder
        </button>
        <input
          ref={directoryInputRef}
          className="visually-hidden"
          type="file"
          multiple
          accept=".wav,.txt"
          aria-label="Choose a folder containing WAV and TXT files"
          onChange={selectFolder}
        />
      </header>

      <div className="evaluation-note">
        <strong>How it works</strong>
        <span>Files are matched by relative path and stem. Each matched pair runs separately; scores and generated audio appear here.</span>
        <span>Audio is sent to the Deepgram and Azure Speech services configured for your backend. Use only synthetic or consented recordings.</span>
      </div>

      {error && <p className="evaluation-error" role="alert">{error}</p>}
      {pairs.length > 0 && (
        <>
          <div className="evaluation-toolbar">
            <p><strong>{pairs.length}</strong> matched {pairs.length === 1 ? "pair" : "pairs"}{unmatchedCount > 0 && <span> · {unmatchedCount} unmatched file(s) ignored</span>}</p>
            <div className="evaluation-actions">
              <button type="button" onClick={() => void runEvaluation("stt")} disabled={running !== null}>Run STT</button>
              <button type="button" onClick={() => void runEvaluation("tts")} disabled={running !== null}>Run TTS</button>
              {running && <button type="button" className="evaluation-cancel" onClick={cancelEvaluation}>Cancel</button>}
            </div>
          </div>
          {running && (
            <p className="evaluation-progress" role="status">
              {running.toUpperCase()} · {progress.done}/{progress.total}
              {progress.current && ` · ${progress.current}`}
            </p>
          )}
          <div className="evaluation-results">
            {pairs.map((pair) => {
              const result = results[pair.key];
              return (
                <article className="evaluation-result" key={pair.key}>
                  <div className="evaluation-result-heading">
                    <div><h2>{pair.name}</h2><span>{pair.language ?? "language not inferred"} · {pair.wav.name}</span></div>
                    <span className="evaluation-size">{(pair.wav.size / 1024 / 1024).toFixed(2)} MB</span>
                  </div>
                  <p className="evaluation-reference"><strong>Reference · {pair.transcript.name}</strong></p>
                  <p className="evaluation-reference-text">{pair.referenceText || "Transcript is empty."}</p>
                  {result?.stt && (
                    <div className="evaluation-metrics">
                      <strong>STT</strong>
                      <span>WER {(result.stt.word_error_rate * 100).toFixed(1)}%</span>
                      <span>Word accuracy {result.stt.word_accuracy_percent.toFixed(1)}%</span>
                      <span>CER {(result.stt.character_error_rate * 100).toFixed(1)}%</span>
                      <p>{result.stt.recognized_transcript || "No transcript returned."}</p>
                    </div>
                  )}
                  {result?.tts && (
                    <div className="evaluation-metrics">
                      <strong>TTS</strong>
                      <span>Generated WER {(result.tts.generated_audio_wer * 100).toFixed(1)}%</span>
                      <span>Reference WER {(result.tts.reference_audio_wer * 100).toFixed(1)}%</span>
                      {result.tts.generated_vs_reference_asr_wer !== null && <span>Generated/reference ASR WER {(result.tts.generated_vs_reference_asr_wer * 100).toFixed(1)}%</span>}
                      <p>{result.tts.generated_audio_asr || "No generated transcript returned."}</p>
                      <audio controls preload="none" src={result.tts.audioUrl}>Generated speech audio</audio>
                      <a href={result.tts.audioUrl} download={`${pair.name}.generated.wav`}>Download generated WAV</a>
                    </div>
                  )}
                  {result?.error && <p className="evaluation-error" role="alert">{result.error}</p>}
                </article>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}

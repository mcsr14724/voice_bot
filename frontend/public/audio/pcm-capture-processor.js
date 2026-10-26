class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.samples = [];
    this.noiseFloor = 0.002;
    this.speechFrames = [];
    this.speechActive = false;
    this.silentFrames = 0;
  }

  process(inputs, outputs) {
    const input = inputs[0];
    const output = outputs[0];
    if (output) output.forEach((channel) => channel.fill(0));
    if (!input || input.length === 0 || input[0].length === 0) return true;

    const frameCount = input[0].length;
    for (let index = 0; index < frameCount; index += 1) {
      let mono = 0;
      for (const channel of input) mono += channel[index] || 0;
      mono /= input.length;
      this.phase += 16000 / sampleRate;
      while (this.phase >= 1) {
        this.samples.push(mono);
        this.phase -= 1;
        if (this.samples.length === 320) this.sendFrame();
      }
    }
    return true;
  }

  sendFrame() {
    let energy = 0;
    let crossings = 0;
    let previous = this.samples[0] || 0;
    for (const sample of this.samples) {
      energy += sample * sample;
      if ((sample >= 0) !== (previous >= 0)) crossings += 1;
      previous = sample;
    }
    const rms = Math.sqrt(energy / this.samples.length);
    const zeroCrossingRate = crossings / this.samples.length;
    const threshold = Math.max(0.008, this.noiseFloor * 2.8);
    const voiceLike = rms > threshold && zeroCrossingRate > 0.005 && zeroCrossingRate < 0.35;
    if (!this.speechActive) {
      this.noiseFloor = Math.min(0.08, this.noiseFloor * 0.98 + rms * 0.02);
      this.speechFrames.push(voiceLike);
      if (this.speechFrames.length > 4) this.speechFrames.shift();
    }

    let speechStarted = false;
    let speechEnded = false;
    if (!this.speechActive && this.speechFrames.filter(Boolean).length >= 3) {
      this.speechActive = true;
      this.silentFrames = 0;
      speechStarted = true;
    } else if (this.speechActive) {
      this.silentFrames = voiceLike ? 0 : this.silentFrames + 1;
      if (this.silentFrames >= 25) {
        this.speechActive = false;
        this.speechFrames = [];
        this.silentFrames = 0;
        speechEnded = true;
      }
    }

    const buffer = new ArrayBuffer(640);
    const view = new DataView(buffer);
    this.samples.forEach((sample, index) => {
      const clipped = Math.max(-1, Math.min(1, sample));
      view.setInt16(index * 2, clipped < 0 ? clipped * 32768 : clipped * 32767, true);
    });
    this.samples = [];
    this.port.postMessage({ buffer, speechStarted, speechEnded }, [buffer]);
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);

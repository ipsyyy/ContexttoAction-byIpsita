// VAD worklet — receives raw Float32 audio at the AudioContext sample rate
// (we create the context at 16 kHz, so resampling happens upstream of us).
// Buffers into 100 ms frames, computes RMS per frame, and posts {pcm, rms} to
// the main thread.

class VadWorklet extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buf = []
    this.frameSize = 1600 // 100 ms at 16 kHz
  }

  process(inputs) {
    const ch = inputs[0]?.[0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) this.buf.push(ch[i])
    while (this.buf.length >= this.frameSize) {
      const slice = this.buf.splice(0, this.frameSize)
      const pcm = Float32Array.from(slice)
      let sumSq = 0
      for (let i = 0; i < pcm.length; i++) sumSq += pcm[i] * pcm[i]
      const rms = Math.sqrt(sumSq / pcm.length)
      this.port.postMessage({ pcm, rms }, [pcm.buffer])
    }
    return true
  }
}

registerProcessor('vad-worklet', VadWorklet)

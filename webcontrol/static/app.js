const statusText = document.querySelector("#statusText");
const taskInput = document.querySelector("#taskInput");
const sendTaskBtn = document.querySelector("#sendTaskBtn");
const abortBtn = document.querySelector("#abortBtn");
const logBox = document.querySelector("#logBox");
const clearLogsBtn = document.querySelector("#clearLogsBtn");
const lastResult = document.querySelector("#lastResult");
const motionList = document.querySelector("#motionList");
const motionName = document.querySelector("#motionName");
const armSide = document.querySelector("#armSide");
const recordSeconds = document.querySelector("#recordSeconds");
const recordMotionBtn = document.querySelector("#recordMotionBtn");
const refreshCameraBtn = document.querySelector("#refreshCameraBtn");
const cameraPreview = document.querySelector("#cameraPreview");
const recordBtn = document.querySelector("#recordBtn");
const rerecordBtn = document.querySelector("#rerecordBtn");
const runVoiceBtn = document.querySelector("#runVoiceBtn");
const discardVoiceBtn = document.querySelector("#discardVoiceBtn");
const voiceText = document.querySelector("#voiceText");
const continuousBtn = document.querySelector("#continuousBtn");

let lastLogId = 0;
let mediaRecorder = null;
let audioChunks = [];
let lastAudioBlob = null;
let continuous = false;
let busy = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function setBusy(value) {
  busy = value;
  sendTaskBtn.disabled = value;
  recordMotionBtn.disabled = value;
}

async function refreshStatus() {
  try {
    const data = await api("/api/status");
    setBusy(data.busy);
    statusText.textContent = data.connected
      ? data.busy
        ? "机器人正在执行任务"
        : "机器人已连接，待命中"
      : "机器人将在首次任务时连接";
    if (data.last_result) lastResult.textContent = data.last_result;
    if (data.last_error) lastResult.textContent = data.last_error;
  } catch (error) {
    statusText.textContent = `状态获取失败：${error.message}`;
  }
}

async function refreshLogs() {
  try {
    const data = await api(`/api/logs?after=${lastLogId}`);
    for (const line of data.lines) {
      lastLogId = Math.max(lastLogId, line.id);
      logBox.textContent += `[${line.time}] ${line.text}\n`;
    }
    if (data.lines.length) logBox.scrollTop = logBox.scrollHeight;
  } catch {
    // 后端未就绪时保持页面可用。
  }
}

async function sendTask(text) {
  const task = text.trim();
  if (!task) return;
  const data = await api("/api/task", {
    method: "POST",
    body: JSON.stringify({ text: task }),
  });
  if (!data.ok) {
    lastResult.textContent = data.message;
  }
  await refreshStatus();
}

async function refreshMotions() {
  const data = await api("/api/motions");
  motionList.innerHTML = "";
  for (const item of data.motions) {
    const row = document.createElement("div");
    row.className = "motion-item";
    const info = document.createElement("div");
    const title = document.createElement("div");
    title.textContent = item.name;
    const meta = document.createElement("div");
    meta.className = "motion-meta";
    meta.textContent = `手臂：${item.arm_side || "unknown"}  时长：${item.duration_s || "-"}s  帧数：${item.frame_count || "-"}`;
    info.append(title, meta);

    const playBtn = document.createElement("button");
    playBtn.textContent = "播放";
    playBtn.onclick = async () => {
      const result = await api("/api/motions/play", {
        method: "POST",
        body: JSON.stringify({ motion_name: item.name, speed: 1.0 }),
      });
      if (!result.ok) lastResult.textContent = result.message;
    };
    row.append(info, playBtn);
    motionList.append(row);
  }
  if (!data.motions.length) {
    motionList.textContent = data.summary || "暂无动作序列。";
  }
}

async function refreshCamera() {
  cameraPreview.src = `/api/camera/preview?t=${Date.now()}`;
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeString = (offset, string) => {
    for (let i = 0; i < string.length; i++) view.setUint8(offset + i, string.charCodeAt(i));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

async function recordWav(seconds = 5) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const audioContext = new AudioContext({ sampleRate: 16000 });
  const source = audioContext.createMediaStreamSource(stream);
  const processor = audioContext.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  source.connect(processor);
  processor.connect(audioContext.destination);
  processor.onaudioprocess = (event) => {
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  };
  await new Promise((resolve) => setTimeout(resolve, seconds * 1000));
  processor.disconnect();
  source.disconnect();
  stream.getTracks().forEach((track) => track.stop());
  await audioContext.close();
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const samples = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    samples.set(chunk, offset);
    offset += chunk.length;
  }
  return encodeWav(samples, 16000);
}

async function transcribeBlob(blob) {
  const form = new FormData();
  form.append("audio", blob, "voice.wav");
  const data = await api("/api/asr", { method: "POST", body: form });
  voiceText.value = data.text || "";
  rerecordBtn.disabled = false;
  runVoiceBtn.disabled = !voiceText.value.trim();
  discardVoiceBtn.disabled = false;
  return voiceText.value.trim();
}

async function recordAndTranscribe() {
  recordBtn.disabled = true;
  recordBtn.textContent = "录音中...";
  try {
    lastAudioBlob = await recordWav(5);
    await transcribeBlob(lastAudioBlob);
  } catch (error) {
    lastResult.textContent = `录音失败：${error.message}`;
  } finally {
    recordBtn.disabled = false;
    recordBtn.textContent = "开始录音";
  }
}

sendTaskBtn.onclick = () => sendTask(taskInput.value);
taskInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") sendTask(taskInput.value);
});

abortBtn.onclick = async () => {
  await api("/api/abort", { method: "POST", body: JSON.stringify({}) });
  await refreshStatus();
};

recordMotionBtn.onclick = async () => {
  const result = await api("/api/motions/record", {
    method: "POST",
    body: JSON.stringify({
      motion_name: motionName.value.trim(),
      arm_side: armSide.value,
      seconds: Number(recordSeconds.value || 5),
      fps: 20,
    }),
  });
  if (!result.ok) lastResult.textContent = result.message;
};

refreshCameraBtn.onclick = refreshCamera;
clearLogsBtn.onclick = () => {
  logBox.textContent = "";
};
recordBtn.onclick = recordAndTranscribe;
rerecordBtn.onclick = recordAndTranscribe;
discardVoiceBtn.onclick = () => {
  voiceText.value = "";
  runVoiceBtn.disabled = true;
  discardVoiceBtn.disabled = true;
};
runVoiceBtn.onclick = () => sendTask(voiceText.value);

continuousBtn.onclick = async () => {
  continuous = !continuous;
  continuousBtn.textContent = continuous ? "连续监听：开" : "连续监听：关";
  while (continuous) {
    await recordAndTranscribe();
    if (!continuous) break;
    await new Promise((resolve) => setTimeout(resolve, 1800));
    if (voiceText.value.trim()) await sendTask(voiceText.value);
    while (busy && continuous) await new Promise((resolve) => setTimeout(resolve, 500));
  }
};

setInterval(refreshStatus, 1000);
setInterval(refreshLogs, 500);
setInterval(refreshMotions, 3000);
refreshStatus();
refreshLogs();
refreshMotions();

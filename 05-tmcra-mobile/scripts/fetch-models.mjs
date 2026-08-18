// Pre-download the pinned TMCRA model assets into .build-cache/models so the
// Gradle fetch tasks see matching checksums and skip their own downloads
// (Gradle's URLConnection does not survive hf-mirror 308 redirects well on CN
// networks; Invoke-WebRequest through the system proxy is reliable).
//
// Usage:  node scripts/fetch-models.mjs
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const modelsDir = path.join(repoRoot, '.build-cache', 'models');

const specs = [
  {
    outputName: 'tmcra-pyannote-segmentation-3.0.int8.onnx',
    sha256: 'd582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d',
    urls: [
      'https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/main/model.int8.onnx',
      'https://hf-mirror.com/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/main/model.int8.onnx',
    ],
  },
  {
    outputName: '3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx',
    sha256: 'bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239',
    urls: [
      'https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx',
      'https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx',
    ],
  },
  {
    outputName: 'tmcra-asr-zipformer-small-zh-2025-04.int8.onnx',
    sha256: '68c9c943840f7d9cf3e8a4970ba50f404feb5277f611fa82b7e72267786fa84a',
    urls: [
      'https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01/resolve/main/model.int8.onnx',
      'https://hf-mirror.com/csukuangfj/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01/resolve/main/model.int8.onnx',
    ],
  },
  {
    outputName: 'tmcra-asr-zipformer-small-zh-2025-04.tokens.txt',
    sha256: '6fed8c6c248516f38e7faa19404b57413e8ce259f1cbc1fa4aebc86eac32fdfd',
    urls: [
      'https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01/resolve/main/tokens.txt',
      'https://hf-mirror.com/csukuangfj/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01/resolve/main/tokens.txt',
    ],
  },
];

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

fs.mkdirSync(modelsDir, { recursive: true });

function download(url, dest) {
  execFileSync('powershell', [
    '-NoProfile', '-Command',
    `[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri '${url}' -OutFile '${dest}' -TimeoutSec 300`,
  ], { stdio: 'inherit' });
}

let ok = 0;
for (const spec of specs) {
  const dest = path.join(modelsDir, spec.outputName);
  if (fs.existsSync(dest) && sha256(dest) === spec.sha256) {
    console.log(`OK (cached) ${spec.outputName}`);
    ok++;
    continue;
  }
  let done = false;
  for (const url of spec.urls) {
    try {
      console.log(`downloading ${spec.outputName} from ${url}`);
      download(url, dest);
      const actual = sha256(dest);
      if (actual === spec.sha256) {
        console.log(`OK ${spec.outputName} (${(fs.statSync(dest).size / 1048576).toFixed(1)} MB)`);
        ok++;
        done = true;
        break;
      }
      console.log(`  checksum mismatch ${actual}, trying next URL`);
      fs.rmSync(dest, { force: true });
    } catch (e) {
      console.log(`  failed: ${e.message.split('\n')[0]}`);
      fs.rmSync(dest, { force: true });
    }
  }
  if (!done) console.log(`FAILED ${spec.outputName}`);
}
console.log(`\n${ok}/${specs.length} models ready`);
process.exit(ok === specs.length ? 0 : 1);

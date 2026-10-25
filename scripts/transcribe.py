"""Transcribe with a LOCAL faster-whisper model; never download a model implicitly."""
import argparse
import dataclasses
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path


def record(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return value
    if hasattr(value, '_asdict'):
        return dict(value._asdict())
    raise TypeError('Unsupported segment representation')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audio', type=Path, required=True)
    p.add_argument('--model-dir', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True, help='A new, empty run directory')
    p.add_argument('--packages', type=Path, help='Optional existing project Python packages')
    p.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    p.add_argument('--language', default='zh')
    p.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    p.add_argument('--compute-type', help='Default int8 for CPU, float16 for CUDA')
    p.add_argument('--threads', type=int, default=8)
    p.add_argument('--beam-size', type=int, default=5)
    p.add_argument('--start', type=float, default=0, help='Source offset in seconds')
    p.add_argument('--duration', type=float, help='Optional selected range, also useful for smoke check')
    a = p.parse_args()
    if not a.audio.is_file() or not (a.model_dir / 'model.bin').is_file():
        p.error('Audio and local model.bin must already exist')
    if not a.ffmpeg:
        p.error('Specify --ffmpeg')
    if not math.isfinite(a.start) or a.start < 0 or (a.duration is not None and
        (not math.isfinite(a.duration) or a.duration <= 0)):
        p.error('Invalid source range')
    if a.threads <= 0 or a.beam_size <= 0:
        p.error('threads and beam-size must be positive')
    if a.output_dir.exists() and any(a.output_dir.iterdir()):
        p.error('Output directory is not empty; use a new run directory to preserve prior work')
    a.output_dir.mkdir(parents=True, exist_ok=True)
    if a.packages:
        sys.path.insert(0, str(a.packages.resolve()))
    os.environ['HF_HOME'] = str(a.output_dir / 'cache')
    os.environ['HF_HUB_OFFLINE'] = '1'
    status_file = a.output_dir / 'status.json'
    config = {'engine': 'faster-whisper', 'model_path': str(a.model_dir.resolve()),
        'language': a.language, 'device': a.device,
        'compute_type': a.compute_type or ('int8' if a.device == 'cpu' else 'float16'),
        'beam_size': a.beam_size, 'vad_filter': True, 'word_timestamps': True,
        'source_offset': a.start, 'requested_duration': a.duration}

    def status(state, **extra):
        status_file.write_text(json.dumps({'state': state, 'config': config, **extra},
            ensure_ascii=False, indent=2), encoding='utf-8')

    status('running')
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        command = [a.ffmpeg, '-nostdin', '-v', 'error']
        if a.start:
            command += ['-ss', str(a.start)]
        command += ['-i', str(a.audio)]
        if a.duration:
            command += ['-t', str(a.duration)]
        command += ['-f', 's16le', '-ac', '1', '-ar', '16000', '-']
        raw = subprocess.check_output(command, stderr=subprocess.PIPE, timeout=900)
        samples = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
        if not samples.size:
            raise ValueError('Audio decoded to zero samples')
        model = WhisperModel(str(a.model_dir.resolve()), device=a.device,
            compute_type=config['compute_type'], cpu_threads=a.threads, local_files_only=True)
        segments, info = model.transcribe(samples, language=a.language,
            beam_size=a.beam_size, vad_filter=True, word_timestamps=True)
        records = []
        with (a.output_dir / 'segments.jsonl').open('w', encoding='utf-8') as stream:
            for segment in segments:
                d = record(segment)
                d['start'] += a.start
                d['end'] += a.start
                d['words'] = [record(w) for w in d.get('words') or []]
                for w in d['words']:
                    w['start'] += a.start
                    w['end'] += a.start
                records.append(d)
                stream.write(json.dumps(d, ensure_ascii=False) + '\n')
                stream.flush()
                print(f'Saved segment {len(records)} through {d["end"]:.1f}s', flush=True)
        (a.output_dir / 'whisper.json').write_text(json.dumps({'config': config,
            'decoded_duration': info.duration, 'segments': records}, ensure_ascii=False, indent=2), encoding='utf-8')
        (a.output_dir / 'transcript.txt').write_text('\n'.join(
            f'[{d["start"]:.1f}–{d["end"]:.1f}] {d["text"]}' for d in records), encoding='utf-8')
        status('complete', decoded_duration=info.duration, segments=len(records))
        print('TRANSCRIPTION COMPLETE', flush=True)
    except Exception as exc:
        status('failed', error_type=type(exc).__name__)
        print('Transcription failed: ' + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

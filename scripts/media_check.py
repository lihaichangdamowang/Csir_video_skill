"""Validate media for ONE video part; no network or automatic redownload."""
import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path


def positive(value):
    n = float(value)
    if not math.isfinite(n) or n <= 0:
        raise argparse.ArgumentTypeError('Expected a finite positive number')
    return n


def main():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--metadata', type=Path, help='yt-dlp metadata for this part')
    source.add_argument('--expected', type=positive, help='Expected duration in seconds')
    p.add_argument('--media', type=Path, action='append', required=True)
    p.add_argument('--threshold', type=positive, default=.05)
    p.add_argument('--ffprobe', default=shutil.which('ffprobe'))
    p.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    p.add_argument('--decode', action='store_true')
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    if not a.ffprobe or (a.decode and not a.ffmpeg):
        p.error('Specify ffprobe and, for --decode, ffmpeg')
    expected = a.expected
    if a.metadata:
        meta = json.loads(a.metadata.read_text(encoding='utf-8-sig'))
        if meta.get('_type') in ('playlist', 'multi_video') or meta.get('entries'):
            p.error('Use metadata for one selected part, not playlist metadata')
        try:
            expected = positive(meta.get('duration'))
        except (TypeError, ValueError, argparse.ArgumentTypeError):
            p.error('Metadata duration is unknown or invalid; do not claim a pass')
    report = {'expected_seconds': expected, 'threshold': a.threshold, 'files': []}
    for file in a.media:
        item = {'file': str(file), 'duration_pass': False, 'decode_pass': None}
        try:
            raw = subprocess.check_output([a.ffprobe, '-v', 'error', '-show_entries',
                'format=duration:stream=codec_name,codec_type,width,height', '-of', 'json', str(file)],
                stderr=subprocess.PIPE, timeout=120)
            probe = json.loads(raw)
            measured = positive(probe['format']['duration'])
            delta = abs(measured - expected) / expected
            item.update(measured_seconds=measured, difference_fraction=delta,
                        duration_pass=delta <= a.threshold, streams=probe.get('streams', []))
            if a.decode:
                decode = subprocess.run([a.ffmpeg, '-nostdin', '-v', 'error', '-i', str(file),
                    '-f', 'null', '-'], capture_output=True,
                    timeout=max(180, min(21600, math.ceil(expected * 2))))
                item['decode_pass'] = decode.returncode == 0 and not decode.stderr.strip()
                if not item['decode_pass']:
                    item['decode_error'] = decode.stderr.decode(errors='replace')[:2000]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError,
                argparse.ArgumentTypeError) as exc:
            item['error_type'] = type(exc).__name__
        report['files'].append(item)
    report['passed'] = all(i['duration_pass'] and
        (i['decode_pass'] is True if a.decode else True) for i in report['files'])
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'files': len(report['files']),
                      'report': str(a.report)}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

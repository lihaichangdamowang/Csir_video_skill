"""Copy an explicit deliverable allowlist, check local HTML links, optionally ZIP it."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import unquote, urlsplit


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.ids = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'id' in a:
            self.ids.append(a['id'])
        for k in ('href', 'src', 'poster'):
            if k in a:
                self.links.append(a[k])


def sensitive_json(obj):
    if isinstance(obj, dict):
        for key, val in obj.items():
            if str(key).lower() in {'cookie', 'cookies', 'authorization', 'proxy-authorization',
                                   'sessdata', 'access_token', 'refresh_token', 'api_key'}:
                return True
            if sensitive_json(val):
                return True
    elif isinstance(obj, list):
        return any(sensitive_json(v) for v in obj)
    return False


def validate_files(root, names):
    files = []
    for name in names:
        if not isinstance(name, str) or '\\' in name:
            raise ValueError('Manifest paths must be relative forward-slash strings')
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or ':' in name:
            raise ValueError('Manifest path is outside the site root')
        file = root.joinpath(*relative.parts).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise ValueError('Missing file or symlink outside the site root')
        if any(part.lower() in {'tools', 'models', '.git', '.env', '__pycache__'} for part in relative.parts):
            raise ValueError('Non-deliverable directory in manifest')
        if re.search(r'cookie|credential|secret', file.name, re.I) or file.suffix.lower() in {'.pem', '.key', '.sqlite'} or file.name.lower().startswith('.env'):
            raise ValueError('Credential-like file in manifest')
        with file.open('rb') as stream:
            head = stream.read(4096)
        if b'# Netscape HTTP Cookie File' in head or b'# HTTP Cookie File' in head:
            raise ValueError('Cookie contents detected; values are not printed')
        if file.suffix.lower() == '.json' and sensitive_json(json.loads(file.read_text(encoding='utf-8-sig'))):
            raise ValueError('Sensitive JSON keys detected; values are not printed')
        files.append((name, file))
    if len({n for n, _ in files}) != len(files):
        raise ValueError('Duplicate manifest entries')
    return files


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True, help='New or empty directory')
    p.add_argument('--zip', type=Path, help='New ZIP file path')
    a = p.parse_args()
    root, out = a.root.resolve(), a.output_dir.resolve()
    try:
        if out == root or root.is_relative_to(out):
            raise ValueError('Output cannot equal or contain source root')
        if out.exists() and any(out.iterdir()):
            raise ValueError('Output directory is not empty; preserve existing artifacts')
        if a.zip and a.zip.exists():
            raise ValueError('ZIP already exists; choose a new output path')
        if a.zip and (a.zip.resolve().is_relative_to(out) or a.zip.suffix.lower() != '.zip'):
            raise ValueError('ZIP must be outside packaged directory and end in .zip')
        manifest = json.loads(a.manifest.read_text(encoding='utf-8-sig'))
        names = manifest.get('files')
        if not isinstance(names, list) or not names:
            raise ValueError('Manifest requires a non-empty files list')
        files = validate_files(root, names)
        pages = {}
        for name, file in files:
            if file.suffix.lower() in {'.html', '.htm'}:
                parser = Links()
                parser.feed(file.read_text(encoding='utf-8-sig'))
                if len(parser.ids) != len(set(parser.ids)):
                    raise ValueError('Duplicate HTML or SVG IDs in ' + name)
                pages[name] = parser
        if 'index.html' not in pages:
            raise ValueError('Manifest must include index.html')
        inventory = {file.resolve() for _, file in files}
        checked = 0
        for name, page in pages.items():
            for link in page.links:
                u = urlsplit(link)
                if u.scheme or u.netloc:
                    continue
                if u.path.startswith('/'):
                    raise ValueError('Root-relative paths do not work reliably offline')
                target = (root / name).parent.joinpath(unquote(u.path)).resolve() if u.path else (root / name).resolve()
                if target not in inventory:
                    raise ValueError('Local HTML link is not in the manifest')
                if u.fragment:
                    key = target.relative_to(root).as_posix()
                    if key in pages and unquote(u.fragment) not in pages[key].ids:
                        raise ValueError('Broken local fragment in ' + name)
                checked += 1
        out.mkdir(parents=True, exist_ok=True)
        for name, file in files:
            dest = out / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, dest)
        result = {'copied_files': len(files), 'checked_local_links': checked,
                  'checks': 'allowlist, path bounds, common credentials, duplicate IDs, HTML local links',
                  'limits': 'Not an exhaustive secret scanner or browser visual/interaction test'}
        (out / 'package-validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if a.zip:
            a.zip.parent.mkdir(parents=True, exist_ok=True)
            shutil.make_archive(str(a.zip.with_suffix('')), 'zip', out)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print('Packaging stopped: ' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

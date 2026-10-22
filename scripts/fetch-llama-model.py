#!/usr/bin/env python3
"""使用 curl 断点续传固定模型，校验后才替换正式文件。"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def verify(path, manifest):
    if not path.is_file() or path.stat().st_size != manifest['size_bytes']:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest() == manifest['sha256']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    args.directory.mkdir(parents=True, exist_ok=True)
    target = args.directory / manifest['filename']
    if verify(target, manifest):
        print('Model already verified.', flush=True)
        return
    partial = target.with_suffix(target.suffix + '.part')
    url = 'https://huggingface.co/{repository}/resolve/{revision}/{filename}?download=true'.format(**manifest)
    subprocess.run([
        'curl', '--fail', '--location', '--continue-at', '-', '--retry', '5',
        '--retry-delay', '5', '--connect-timeout', '20', '--speed-time', '90',
        '--speed-limit', '1024', '--silent', '--show-error', '--output', str(partial), url
    ], check=True)
    if not verify(partial, manifest):
        raise RuntimeError('Model size or SHA-256 mismatch; partial file preserved for inspection.')
    partial.chmod(0o644)
    partial.replace(target)
    print('Model download verified and activated.', flush=True)


if __name__ == '__main__':
    main()

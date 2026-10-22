"""不可变发布与崩溃恢复；当前发布清单是已上线源码的唯一依据。"""
from contextlib import contextmanager
import fcntl
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.new')
    with tmp.open('w') as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())
    tmp.replace(path)
    sync_dir(path.parent)


@contextmanager
def decision_lock(base):
    # 文件独立于原子替换的 commands.json，API 与发布器共享同一把锁。
    with (base / 'control/decision.lock').open('a') as stream:
        os.chmod(stream.name, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def git(base, *args, env=None, binary=False):
    return subprocess.run(['git', '-C', str(base / 'history'), *args], env=env,
                          capture_output=True, check=True, timeout=120,
                          text=not binary).stdout


def export_source(base, version, target):
    target.mkdir(parents=True, exist_ok=False)
    data = git(base, 'archive', version, 'game', 'NOTES.md', binary=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(target, filter='data')


def prepare_commit(base, candidate, parent, number, task):
    # 使用私有索引与 commit-tree；准备失败不会改变 HEAD、工作区或已发布指针。
    index = candidate.parent / 'git-index'
    index.unlink(missing_ok=True)
    env = {**os.environ, 'GIT_INDEX_FILE': str(index), 'GIT_WORK_TREE': str(candidate)}
    try:
        git(base, 'read-tree', parent, env=env)
        git(base, 'add', '--all', '--', 'game', 'NOTES.md', env=env)
        tree = git(base, 'write-tree', env=env).strip()
        return git(base, 'commit-tree', tree, '-p', parent, '-m', f'round {number}: {task}').strip()
    finally:
        index.unlink(missing_ok=True)


def versioned_html(text, release_id):
    if re.search(r'<\s*base\b', text, re.I):
        raise ValueError('Game source cannot supply its own base URL')
    text, count = re.subn(r'(<head\b[^>]*>)',
                         lambda m: m[0] + f'<base href="/releases/{release_id}/game/">',
                         text, count=1, flags=re.I)
    if count != 1:
        raise ValueError('Game index must contain a head element')
    return text


def stage(base, candidate, release_id, version, state, contracts, outputs):
    parent = base / 'public/releases'
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / release_id
    temporary = parent / ('.preparing-' + release_id)
    shutil.rmtree(temporary, ignore_errors=True)
    if target.exists():
        # 同一轮提交已完成准备时可重试，但不覆写不可变资源。
        manifest = json.loads((target / 'manifest.json').read_text())
        if manifest['version'] != version:
            raise ValueError('Release identity collision')
        return target
    shutil.copytree(candidate / 'game', temporary / 'game')
    index = temporary / 'game/index.html'
    index.write_text(versioned_html(index.read_text(), release_id))
    for name in ('desktop.png', 'mobile.png'):
        if (outputs / name).is_file():
            shutil.copyfile(outputs / name, temporary / name)
    atomic(temporary / 'manifest.json', {'version': version, 'release_id': release_id,
                                        'state': state, 'contracts': contracts})
    for path in [temporary, *temporary.rglob('*')]:
        path.chmod(0o755 if path.is_dir() else 0o644)
        if path.is_file():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
    sync_dir(temporary / 'game')
    sync_dir(temporary)
    temporary.rename(target)
    sync_dir(parent)
    return target


def current(base):
    link = base / 'public/current'
    if not link.is_symlink():
        return None
    target = link.resolve(strict=True)
    if target.parent != (base / 'public/releases').resolve():
        raise ValueError('Invalid current release link')
    file = target / 'manifest.json'
    return json.loads(file.read_text()) if file.exists() else None


def finish(base, manifest):
    version = manifest['version']
    # 以下都是可重复操作；即使进程再次中断，pending 仍会触发恢复。
    git(base, 'update-ref', 'refs/starharbor/accepted', version)
    git(base, 'reset', '--hard', version)
    atomic(base / 'contracts.json', manifest['contracts'], 0o600)
    atomic(base / 'public/state.json', manifest['state'])
    atomic(base / 'checkpoint.json', {}, 0o600)


def recover(base):
    journal = base / 'pending.json'
    manifest = current(base)
    pending = json.loads(journal.read_text()) if journal.exists() else None
    state_path = base / 'public/state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if manifest and (pending or state.get('release_id') != manifest['release_id']):
        # 指针已经切换则补齐成功记录；尚未切换时保留旧发布与候选供重试。
        if not pending or pending['release_id'] == manifest['release_id']:
            finish(base, manifest)
            state = manifest['state']
    if journal.exists():
        journal.unlink()
        sync_dir(base)
    return state


def activate(base, release_id, permitted):
    manifest = json.loads((base / 'public/releases' / release_id / 'manifest.json').read_text())
    atomic(base / 'pending.json', {'release_id': release_id}, 0o600)
    link = base / 'public/current.new'
    link.unlink(missing_ok=True)
    link.symlink_to(Path('releases') / release_id, target_is_directory=True)
    try:
        # 锁内只做最终许可检查与指针切换；stop 返回后不会再发布这一版。
        with decision_lock(base):
            if not permitted():
                raise InterruptedError('Publication cancelled by control or deadline')
            link.replace(base / 'public/current')
        sync_dir(base / 'public')
        finish(base, manifest)
        (base / 'pending.json').unlink()
        sync_dir(base)
    except BaseException:
        # 当前指针决定事务结果；调用者须先 recover，不能一律标记失败。
        link.unlink(missing_ok=True)
        raise
    return manifest['state']


def prune(base, protected_candidate=None):
    # 清理不参与事务。保留周期内全部不可变资源，使已打开页面仍可加载模块。
    current_link = (base / 'public/current').resolve()
    for path in (base / 'public/releases').glob('.preparing-*'):
        if path != current_link:
            shutil.rmtree(path)
    paths = sorted((base / 'rounds').iterdir())
    for old in paths[:-30]:
        if protected_candidate is None or old != protected_candidate.parent:
            shutil.rmtree(old)

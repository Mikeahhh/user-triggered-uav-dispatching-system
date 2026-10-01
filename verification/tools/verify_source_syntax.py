from pathlib import Path
import os
import tokenize

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {'node_modules', 'Fast-Drone-250', '.git', '__pycache__', '.venv',
            'local-results', 'regenerated', 'external', 'records', 'venv', 'build', 'devel', 'dist', 'history', 'releases', '.gradle', 'Pods'}


def main():
    checked = 0
    for directory, dirs, names in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in EXCLUDED and not name.startswith('.venv')]
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_symlink():
                continue
            if path.suffix not in {'.py', '.spec'} and not name.startswith('windows_version_info'):
                continue
            with tokenize.open(path) as handle:
                source = handle.read()
            compile(source, str(path), 'exec')
            checked += 1
    if checked == 0:
        raise RuntimeError('no first-party Python sources found')
    print('PASS: syntax compiled for {} first-party Python/packaging files; none imported'.format(checked))


if __name__ == '__main__':
    main()

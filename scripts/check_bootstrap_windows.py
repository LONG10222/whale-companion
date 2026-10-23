"""CI clean-venv smoke check; no personal config, account, server or credential setup."""
from pathlib import Path
import platform
import subprocess
import tempfile
from deploy_windows import install, installed, verify_dependencies


def main():
    if platform.system() != 'Windows':
        raise SystemExit('This smoke check requires Windows.')
    source = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix='whale bootstrap ') as temporary:
        root = Path(temporary) / '鲸娅 clean install'
        root.mkdir()
        (root / 'requirements.txt').write_bytes((source / 'requirements.txt').read_bytes())
        install(root, accepted=True)
        assert installed(root), 'Clean installation was not verified'
        assert verify_dependencies(root), 'Expected installed package metadata'
        calls = []
        def record(command, **kwargs):
            calls.append(command)
            return subprocess.run(command, **kwargs)
        install(root, accepted=False, run=record)
        assert not any('install' in command for command in calls), 'Repeated run tried to install again'
        assert not list(root.glob('*.local.json')), 'Installer unexpectedly created account configuration'
        print('Windows clean isolated venv install and no-download repeat passed; no account setup or server start.')


if __name__ == '__main__':
    main()

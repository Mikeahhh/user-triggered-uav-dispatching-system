from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files


component = Path(SPECPATH).resolve().parent

datas = (
    collect_data_files('customtkinter')
    + collect_data_files('tkintermapview')
    + [(str(component / 'assets/icon.ico'), '.')]
)


a = Analysis(
    [str(component / 'main.py')],
    pathex=[str(component / 'src')],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'paho.mqtt.client',
        'paho.mqtt',
        'firebase_admin',
        'google.cloud',
        'google.auth',
        'google.auth.transport.requests',
        'app_metadata',
        'rescue_event_manager',
        'priority_scheduler',
        'sos_pattern',
        'rescue_record_protocol',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ground_station_V7',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(component / 'assets/icon.ico'),
    version=str(Path(SPECPATH) / 'windows_version_info_v7.txt'),
)

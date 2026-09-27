from PyInstaller.utils.hooks import collect_data_files


datas = (
    collect_data_files('customtkinter')
    + collect_data_files('tkintermapview')
)


a = Analysis(
    ['ground_station.py'],
    pathex=[],
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
        'ground_station_demo',
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
    icon='icon.ico',
    version='windows_version_info_v7.txt',
)

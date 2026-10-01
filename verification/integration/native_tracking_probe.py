import json
from pathlib import Path
import subprocess
import tempfile


class NativeTrackingProbe:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='android-tracking-contract-')
        code = (Path(__file__).resolve().parents[2] / "code")
        cache = Path.home() / '.gradle/caches/modules-2/files-2.1'
        dependencies = (
            ('org.jetbrains.kotlin', 'kotlin-compiler-embeddable', '2.1.20'),
            ('org.jetbrains.kotlin', 'kotlin-stdlib', '2.1.20'),
            ('org.jetbrains.kotlin', 'kotlin-script-runtime', '2.1.20'),
            ('org.jetbrains.kotlin', 'kotlin-reflect', '1.6.10'),
            ('org.jetbrains.intellij.deps', 'trove4j', '1.0.20200330'),
            ('org.jetbrains.kotlinx', 'kotlinx-coroutines-core-jvm', '1.8.0'),
            ('org.jetbrains', 'annotations', '23.0.0'),
            ('org.json', 'json', '20180813'),
        )
        jars = []
        for group, artifact, version in dependencies:
            paths = list((cache / group / artifact / version).glob('*/*.jar'))
            if len(paths) != 1:
                self.close()
                raise RuntimeError(f'Cached Kotlin dependency unavailable: {artifact}:{version}')
            jars.append(str(paths[0]))
        self.java = '/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java'
        self.classpath = ':'.join(jars)
        source = code / 'mobile_application/android/app/src/main/java/com/fypproject/tracking/TrackingCore.kt'
        result = subprocess.run([
            self.java, '-cp', self.classpath, 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler',
            '-no-stdlib', '-no-reflect', '-classpath', self.classpath, '-d', self.temporary.name,
            str(source), str(Path(__file__).with_suffix('.kt')),
        ], text=True, capture_output=True, timeout=60)
        if result.returncode:
            self.close()
            raise RuntimeError('Native tracking probe compilation failed: ' + result.stderr)

    def evaluate(self, arguments):
        result = subprocess.run([
            self.java, '-cp', self.temporary.name + ':' + self.classpath,
            'com.fypproject.tracking.Native_tracking_probeKt',
        ], input=json.dumps(arguments), text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Native tracking probe failed: ' + result.stderr)
        return json.loads(result.stdout)

    def close(self):
        self.temporary.cleanup()

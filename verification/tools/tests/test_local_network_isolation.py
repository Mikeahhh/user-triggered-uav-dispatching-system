import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from local_verification import isolated_environment, new_result_directory


ROOT = Path(__file__).resolve().parents[3]


class LocalNetworkIsolationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = isolated_environment(self.directory.name, sys.executable)

    def test_environment_removes_live_configuration_and_proxy_inheritance(self):
        env = isolated_environment(self.directory.name, sys.executable, {
            "GS_FIREBASE_CREDENTIALS": "/live/private.json", "GOOGLE_APPLICATION_CREDENTIALS": "/live/key.json",
            "MQTT_BROKER": "live.invalid", "UAV_RESCUE_TOKEN": "live-token", "HTTPS_PROXY": "http://proxy.invalid",
            "NODE_OPTIONS": "--require /live/bootstrap.cjs", "PYTHONPATH": "/live/python", "PATH": "/bin",
        })
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
        self.assertNotIn("UAV_RESCUE_TOKEN", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("/live/", " ".join(env.values()))
        self.assertEqual(env["MQTT_BROKER"], "127.0.0.1")
        self.assertEqual(env["MASS26_LOCAL_VERIFICATION"], "1")

    def test_python_guard_blocks_dns_external_connections_and_wildcard_listeners(self):
        program = '''
import socket
for operation in (
    lambda: socket.getaddrinfo("blocked.invalid", 443),
    lambda: socket.create_connection(("192.0.2.1", 9)),
    lambda: socket.socket().bind(("0.0.0.0", 0)),
    lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"synthetic", ("192.0.2.1", 9)),
):
    try:
        operation()
    except PermissionError as error:
        assert "non-loopback" in str(error)
    else:
        raise AssertionError("unguarded network operation")
print("blocked")
'''
        result = subprocess.run([sys.executable, "-c", program], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "blocked")

    def test_python_subprocess_inherits_guard_and_loopback_http_works(self):
        program = '''
import http.client, http.server, socket, subprocess, sys, threading
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"synthetic")
    def log_message(self, *args): pass
server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.handle_request); thread.start()
client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3, source_address=("", 0))
client.request("GET", "/"); assert client.getresponse().read() == b"synthetic"
client.close(); thread.join(); server.server_close()
child = subprocess.run([sys.executable, "-c", "import socket; socket.getaddrinfo('blocked.invalid', 443)"], capture_output=True)
assert child.returncode != 0 and b"non-loopback" in child.stderr
print("loopback and inheritance passed")
'''
        result = subprocess.run([sys.executable, "-c", program], env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("loopback and inheritance passed", result.stdout)

    def test_node_guard_blocks_external_calls_and_allows_loopback(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for local three-end verification")
        program = '''
const assert = require('node:assert');
const net = require('node:net');
const dns = require('node:dns');
const http = require('node:http');
const childProcess = require('node:child_process');
for (const operation of [
  () => dns.lookup('blocked.invalid', () => {}),
  () => net.connect({host: '192.0.2.1', port: 9}),
  () => net.createServer().listen(0),
  () => fetch('https://blocked.invalid/'),
]) assert.throws(operation, /non-loopback/);
const child = childProcess.spawnSync(process.execPath, ['-e', "require('node:net').connect({host:'192.0.2.1',port:9})"]);
assert.notStrictEqual(child.status, 0); assert.match(child.stderr.toString(), /non-loopback/);
const server = http.createServer((_request, response) => response.end('synthetic'));
server.listen(0, '127.0.0.1', async () => {
  try {
    const response = await fetch('http://127.0.0.1:' + server.address().port);
    assert.strictEqual(await response.text(), 'synthetic');
    console.log('loopback and inheritance passed');
  } catch (error) { console.error(error); process.exitCode = 1; }
  finally { server.closeAllConnections(); server.close(); }
});
'''
        result = subprocess.run([node, "-e", program], env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("loopback and inheritance passed", result.stdout)

    def test_archived_and_existing_output_directories_cannot_be_used(self):
        for path in (ROOT, ROOT / "verification/records/new-results", ROOT / "code/new-results",
                     ROOT / "simulation/output/new-results", ROOT / "simulation/verification/new-results"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                new_result_directory(path)
        with self.assertRaises(FileExistsError):
            new_result_directory(self.directory.name)
        output = new_result_directory(Path(self.directory.name) / "new-results")
        self.assertTrue(output.is_dir())


if __name__ == "__main__":
    unittest.main()

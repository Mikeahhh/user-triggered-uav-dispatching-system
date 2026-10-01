import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "verification/tools/local_network_guard"
NETWORK_PREFIXES = ("GS_", "MQTT_", "UAV_", "FIREBASE_", "GOOGLE_", "GCLOUD_",
                    "AWS_", "AZURE_", "MASS26_")
NETWORK_VARIABLES = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                     "http_proxy", "https_proxy", "all_proxy", "no_proxy",
                     "NODE_OPTIONS", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
                     "SSLKEYLOGFILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                     "NODE_EXTRA_CA_CERTS"}


def isolated_environment(output, python, source=None):
    source = os.environ if source is None else source
    env = {key: value for key, value in source.items()
           if key not in NETWORK_VARIABLES and not key.startswith(NETWORK_PREFIXES)}
    state = Path(output).resolve() / "synthetic-state"
    env.update({
        "MASS26_LOCAL_VERIFICATION": "1",
        "MASS26_PYTHON": str(python),
        "MASS26_GROUND_PYTHON": str(python),
        "PYTHONPATH": os.pathsep.join((str(GUARD), str(ROOT / "verification/tools"))),
        "NODE_OPTIONS": '--require "' + str(GUARD / "node_guard.cjs") + '"',
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "GS_STATE_DIR": str(state / "ground"),
        "GS_FIREBASE_DATABASE_URL": "https://local-verification-default-rtdb.firebaseio.com",
        "GS_FIREBASE_CREDENTIALS": str(state / "nonexistent-synthetic-credentials.json"),
        "MQTT_BROKER": "127.0.0.1",
        "MQTT_PORT": "1",
        "UAV_RESCUE_BIND_HOST": "127.0.0.1",
        "UAV_RESCUE_MQTT_BROKER": "127.0.0.1",
        "UAV_RESCUE_MQTT_PORT": "1",
        "UAV_RESCUE_DATA_DIR": str(state / "uav-records"),
        "UAV_EXECUTION_JOURNAL_PATH": str(state / "uav-executions.json"),
    })
    return env


def new_result_directory(path):
    output = Path(path).resolve()
    for protected in (ROOT / "verification", ROOT / "code", ROOT / "docs",
                      ROOT / "simulation/output", ROOT / "simulation/paper_current",
                      ROOT / "simulation/data", ROOT / "simulation/reference", ROOT / "simulation/verification"):
        protected = protected.resolve()
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("Verification results must be outside archived inputs, results and source directories")
    output.mkdir(parents=True, exist_ok=False)
    return output

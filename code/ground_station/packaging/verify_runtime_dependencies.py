from importlib.metadata import PackageNotFoundError, version


EXPECTED_RUNTIME_VERSIONS = {
    "customtkinter": "5.2.2",
    "tkintermapview": "1.29",
    "firebase-admin": "7.4.0",
    "paho-mqtt": "2.1.0",
}


def verify_runtime_dependencies() -> None:
    failures = []
    for distribution, expected in EXPECTED_RUNTIME_VERSIONS.items():
        try:
            actual = version(distribution)
        except PackageNotFoundError:
            failures.append(f"{distribution}: missing (expected {expected})")
            continue
        if actual != expected:
            failures.append(f"{distribution}: {actual} (expected {expected})")
    if failures:
        raise SystemExit("Runtime dependency lock mismatch:\n" + "\n".join(failures))


if __name__ == "__main__":
    verify_runtime_dependencies()
    print("Runtime dependency lock verified")

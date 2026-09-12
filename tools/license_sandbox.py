"""Create local test keys and licenses; keep all output in ignored artifacts."""
import argparse
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, load_pem_private_key

from scleaner.licensing import LicenseConfig, LicenseManager, encode64
from scleaner.storage import Storage
from scleaner_server.signing import issue_license

ROOT = Path(__file__).resolve().parents[1]


def initialize(directory: Path, port=8765):
    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Sandbox keys must stay inside this project's ignored artifacts directory")
    directory.mkdir(parents=True, exist_ok=True)
    private_path = directory / "issuer-private.pem"
    if private_path.exists():
        private_key = load_pem_private_key(private_path.read_bytes(), password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise ValueError("Expected an Ed25519 test key")
    else:
        private_key = Ed25519PrivateKey.generate()
        with private_path.open("xb") as stream:
            stream.write(private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    config = {"environment": "test", "public_keys": {"local-test": encode64(private_key.public_key().public_bytes_raw())},
              "server_url": f"http://127.0.0.1:{port}"}
    config_path = directory / "client-test.json"
    Storage(directory).write_json(config_path, config)
    return private_key, config_path


def main():
    parser = argparse.ArgumentParser(description="Initialize SCleaner TEST licensing; no production licenses")
    parser.add_argument("--directory", type=Path, default=ROOT / "artifacts" / "pro-sandbox")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--issue-for", type=Path, help="Create an importable test license for this application data directory")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port between 1024 and 65535")
    key, config_path = initialize(args.directory, args.port)
    print(f"Public test configuration: {config_path}")
    if args.issue_for:
        target = args.issue_for.resolve()
        if not target.is_relative_to((ROOT / "artifacts").resolve()):
            parser.error("Use an isolated application data directory inside artifacts")
        manager = LicenseManager(Storage(target), LicenseConfig.test_file(config_path))
        document = issue_license(key, "local-test", manager.device_id())
        output = config_path.parent / "test-license.sclicense"
        output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Importable TEST license: {output}")
        print(f"Application data directory: {target}")


if __name__ == "__main__":
    main()

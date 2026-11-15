from pathlib import Path
import sys
import csv
import io
import json
import threading
import time
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
TREE = ROOT
BASE = ROOT
sys.path[:0] = [str(TREE / "source"), str(TREE / "product/desktop")]
from memorive_settings.capability import CapabilityProjection
from memorive_settings.contracts import canonical_sha256, default_settings, utc_now
from memorive_settings.controller import SettingsController
from memorive_settings.credentials import CredentialReferenceManager
from memorive_settings.directory_policy import DirectoryPolicy
from memorive_settings.external_sources import ExternalDataSources, default_sources, normalize_payload
from memorive_settings.livebench_reference import CATEGORY_METRICS
from memorive_settings.model_validation import HttpResponse
from memorive_settings.service_adapter import SettingsServiceAdapter
from memorive_settings.store import SettingsStore
from runtime import ProductApi


def model(index):
    vendor, prefix = [("openai", "gpt"), ("deepseek", "deepseek"), ("qwen", "qwen"), ("google", "gemini")][index % 4]
    return vendor, prefix + "-fixture-" + str(index).zfill(3)


def payloads(count=12, *, delta=0):
    stamp = utc_now()[:10]
    categories = {key: [f"t{i}"] for i, key in enumerate(CATEGORY_METRICS)}
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["model", *[v[0] for v in categories.values()]])
    for i in range(count):
        base = model(i)[1]
        writer.writerow([base + "-max", *([min(100, (i * 7 + delta) % 101)] * 7)])
    writer.writerow([model(0)[1] + "-low", *([1] * 7)])
    writer.writerow([model(0)[1] + "-ultra", *([99] * 7)])
    live = {"release": stamp, "categories": categories, "table_csv": stream.getvalue()}
    epoch = io.BytesIO()
    with zipfile.ZipFile(epoch, "w") as archive:
        csvfile = io.StringIO()
        w = csv.writer(csvfile)
        w.writerow(["Model version", "Display name", "ECI Score", "Release date"])
        for i in range(count):
            w.writerow([model(i)[1], "合成模型 " + str(i), 140+i, stamp])
        archive.writestr("epoch_capabilities_index.csv", csvfile.getvalue())
    api = {"data": [{"id": vendor + "/" + base, "name": "合成模型 " + str(i),
                     "context_length": 32768, "pricing": {"prompt": str((i+1)*0.0000001), "completion": str((i+1)*0.0000003)},
                     "architecture": {"input_modalities": ["text"]}, "supported_parameters": ["temperature"]}
                    for i in range(count) for vendor, base in [model(i)]]}
    fx = ('<gesmes:Envelope xmlns:gesmes="urn:test" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
          '<Cube><Cube time="' + stamp + '"><Cube currency="USD" rate="1.2"/><Cube currency="CNY" rate="8.4"/>'
          '<Cube currency="JPY" rate="180"/></Cube></Cube></gesmes:Envelope>').encode()
    return {"artificial-analysis": live, "epoch-ai": epoch.getvalue(), "openrouter": api, "exchange-rates": fx}


class Transport:
    def __init__(self, count=12):
        self.data = payloads(count)
        self.calls = []
        self.fail = set()
        self.block = None
        self.entered = threading.Event()

    def request(self, **kwargs):
        self.calls.append(kwargs)
        self.entered.set()
        if self.block is not None and not self.block.wait(5):
            raise TimeoutError("bounded test timeout")
        url = kwargs["url"]
        if "livebench" in url.lower():
            key = "artificial-analysis"
            data = self.data[key]
            body = ('export const RELEASES = ["' + data["release"] + '"];').encode() if url.endswith("constants.js") else (
                json.dumps(data["categories"]).encode() if url.endswith(".json") else data["table_csv"].encode())
        elif "epoch.ai" in url:
            key, body = "epoch-ai", self.data["epoch-ai"]
        elif "openrouter.ai" in url:
            key, body = "openrouter", json.dumps(self.data["openrouter"]).encode()
        elif "ecb.europa.eu" in url:
            key, body = "exchange-rates", self.data["exchange-rates"]
        else:
            raise AssertionError("UNEXPECTED_ENDPOINT")
        if key in self.fail:
            return HttpResponse(503, b"fixture unavailable")
        return HttpResponse(200, body)


def setup(root, count=12, seed=True):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    transport = Transport(count)
    store = SettingsStore(root / "profile", default_factory=lambda: default_settings(workspace_root=str(root), artifact_root=str(root)))
    external = ExternalDataSources(root / "profile/external_data", transport=transport, environment={})
    controller = SettingsController(store=store, credentials=CredentialReferenceManager(),
        capabilities=CapabilityProjection([], revision="leaderboard-test"), directory_policy=DirectoryPolicy([root]),
        external_data_sources=external)
    adapter = SettingsServiceAdapter(controller)
    api = ProductApi.__new__(ProductApi)
    api._product_call_lock = threading.RLock()
    api._service = adapter
    if seed:
        seed_sources(external, transport.data)
    return api, controller, external, transport


def seed_sources(external, data):
    for config in external._load()["sources"]:
        records = normalize_payload(config, data[config["source_id"]])
        snapshot = {"schema_version": "ExternalModelReference-v1", "config_sha256": canonical_sha256(config),
                    "source_identity_sha256": external._source_identity(config),
                    "retrieved_at": utc_now(), "records": records, "synthetic_fixture": True}
        snapshot["sha256"] = canonical_sha256(snapshot)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = external.root / "snapshots" / config["source_id"] / (stamp + "-fixture.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        external.writer._atomic_write(target, snapshot)


def wait_refresh(controller):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        state = controller.leaderboard_get()
        if state["refresh"]["status"] != "RUNNING":
            return state
        time.sleep(0.01)
    raise AssertionError("REFRESH_DID_NOT_FINISH")

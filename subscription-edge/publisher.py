#!/usr/bin/python3
"""将本机 Sub-Store 导出和已校验规则发布到 Cloudflare，凭据仅从私有配置读取。"""
import argparse
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request
import urllib.error

import yaml

MAX_RULE = 16 * 1024 * 1024
MAX_YAML = 384 * 1024
LOG = logging.getLogger("subscription-edge")


class PublicationHTTPError(RuntimeError):
    def __init__(self, status):
        super().__init__("Publication HTTP error")
        self.status = status


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream)
    temporary.replace(path)


def request(url, *, body=None, token=None):
    headers = {"User-Agent": "LF-Subscription-Publisher/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method="PUT" if body is not None else "GET")
    # 发布配置是受信任的本地配置；不继承环境代理，也不允许重定向泄露发布密钥。
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=45) as response:
            data = response.read(MAX_YAML + 1)
            if body is None and len(data) > MAX_YAML:
                raise ValueError("Source subscription too large")
            return data, response.headers
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise PublicationHTTPError(status) from None


def prepare_subscription(source, rule_directory, public_base, updated_at, userinfo):
    data = yaml.safe_load(source)
    if not isinstance(data, dict) or not data.get("proxies") or not data.get("rules"):
        raise ValueError("Invalid subscription source")
    names = {proxy["name"] for proxy in data["proxies"]}
    if len(names) != len(data["proxies"]):
        raise ValueError("Duplicate proxy name")
    if not any(name.startswith("OCI-") for name in names) or not any(name.startswith("Google-") for name in names):
        raise ValueError("Missing node family")
    providers = data.get("rule-providers", {})
    if not 1 <= len(providers) <= 64:
        raise ValueError("Invalid provider count")
    rules, blobs = [], {}
    directory = Path(rule_directory).resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text())["providers"]
    for name, provider in providers.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or provider.get("type") != "http" or provider.get("format") != "mrs":
            raise ValueError("Unsupported rule provider")
        filename = name + ".mrs"
        path = directory / filename
        if path.resolve().parent != directory or not 12 < path.stat().st_size <= MAX_RULE:
            raise ValueError("Invalid rule file")
        blob = path.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        if blob[:4] != bytes.fromhex("28b52ffd") or digest != manifest[name]["sha256"] or len(blob) != manifest[name]["bytes"]:
            raise ValueError("Rule integrity validation failed")
        provider["url"] = public_base + "/rules/" + digest + "/" + filename
        provider["proxy"] = "DIRECT"
        rules.append({"name": filename, "sha256": digest, "bytes": len(blob)})
        blobs[digest] = blob
    # 保留代理参数、分组和分流语义，只删除此前为规则镜像添加的 IPv4 固定解析。
    data.get("hosts", {}).pop("oci.lfdou.dpdns.org", None)
    if not data.get("hosts"):
        data.pop("hosts", None)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    if len(text.encode()) > MAX_YAML:
        raise ValueError("Published subscription too large")
    snapshot = {"schema": 1, "yaml": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "updated_at": updated_at, "subscription_userinfo": userinfo, "rules": rules}
    return snapshot, blobs


def publish(config, transport=request):
    base = config["publish_base"].rstrip("/")
    if urllib.parse.urlsplit(base).scheme != "https" or urllib.parse.urlsplit(config["public_base"]).scheme != "https":
        raise ValueError("HTTPS publication required")
    source_url = config["source_url"]
    parsed = urllib.parse.urlsplit(source_url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 3000:
        raise ValueError("Source must be local Sub-Store")
    state_file = Path(config["state_file"])
    previous = json.loads(state_file.read_text()) if state_file.exists() else {}
    source, headers = transport(source_url)
    snapshot, blobs = prepare_subscription(source, config["rules_directory"], config["public_base"].rstrip("/"),
                                           int(time.time()), headers.get("Subscription-Userinfo", ""))
    uploaded = set(previous.get("uploaded", []))
    count = 0
    for digest, blob in blobs.items():
        if digest in uploaded:
            continue
        transport(base + "/__publish/rules/" + digest, body=blob, token=config["publish_token"])
        uploaded.add(digest)
        count += 1
    body = json.dumps(snapshot, ensure_ascii=False).encode()
    try:
        transport(base + "/__publish/main", body=body, token=config["publish_token"])
    except PublicationHTTPError as error:
        if error.status != 409:
            raise
        # KV 缺项时补传当前规则，等传播后再提交，不依赖本机旧上传记录。
        for digest, blob in blobs.items():
            transport(base + "/__publish/rules/" + digest, body=blob, token=config["publish_token"])
        time.sleep(65)
        transport(base + "/__publish/main", body=body, token=config["publish_token"])
    state = {"updated_at": snapshot["updated_at"], "sha256": snapshot["sha256"], "uploaded": sorted(uploaded),
             "rule_count": len(snapshot["rules"]), "bytes": len(snapshot["yaml"].encode()), "new_rules": count}
    atomic_json(state_file, state)
    LOG.info("Published subscription: providers=%d bytes=%d new_rules=%d", state["rule_count"], state["bytes"], count)
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/subscription-edge/config.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = json.loads(Path(args.config).read_text())
    lock_path = Path(config["state_file"]).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.info("Publisher already running")
            return
        try:
            publish(config)
        except Exception as error:
            # urllib 的异常文本可能带源站 token；运维日志只记录错误类型。
            LOG.error("Publication failed: %s; previous snapshot preserved", type(error).__name__)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()

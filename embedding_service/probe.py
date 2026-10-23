"""Run explicitly from a Databricks notebook terminal or an authenticated machine.

No writes, database access, model downloads or paid inference providers.
"""

import argparse
import json
import math
import os
from pathlib import Path
import resource
import statistics
import time
from urllib.parse import urlparse

import requests
from runtime import Encoder

FIXTURES = [
    ("query", "How does retrieval augmented generation improve factuality?"),
    ("query", "What are the limitations of vector similarity search?"),
    (
        "document",
        "Retrieval augmented generation combines retrieved evidence with language model generation.",
    ),
    (
        "document",
        "A vector index finds related passages using embedding similarity. " * 18,
    ),
]


def compare(reference, candidate):
    if len(reference) != 768 or len(candidate) != 768:
        raise ValueError("Wrong vector dimension")
    if not all(math.isfinite(x) for x in reference + candidate):
        raise ValueError("Non-finite vector")
    a = math.sqrt(sum(x * x for x in reference))
    b = math.sqrt(sum(x * x for x in candidate))
    if not math.isclose(a, 1, abs_tol=1e-4) or not math.isclose(b, 1, abs_tol=1e-4):
        raise ValueError("Vectors must be normalized")
    cosine = sum(x * y for x, y in zip(reference, candidate)) / (a * b)
    error = max(abs(x - y) for x, y in zip(reference, candidate))
    return {
        "cosine": cosine,
        "max_abs_error": error,
        "passed": cosine >= 0.9999 and error <= 0.001,
    }


def main(argv=None, auth=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-path", required=True, help="Existing ingestion model snapshot"
    )
    parser.add_argument(
        "--url", help="HTTPS Databricks App origin; omit for local CPU benchmark"
    )
    parser.add_argument(
        "--version", help="Expected App snapshot fingerprint; required with --url"
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.url:
        url = urlparse(args.url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
            or not args.version
        ):
            parser.error("Provide an HTTPS App origin and --version")
        if auth is None:
            from databricks.sdk.core import Config

            auth = Config()
    encoder = Encoder()
    encoder.load(args.model_path)
    report = {"local_load_seconds": encoder.load_seconds, "results": []}
    session = requests.Session()
    if args.url:
        response = session.get(
            args.url.rstrip("/") + "/api/ready",
            headers=auth.authenticate(),
            timeout=30,
            allow_redirects=False,
        )
        response.raise_for_status()
        readiness = response.json()
        if (
            readiness.get("state") != "ready"
            or readiness.get("version") != args.version
        ):
            raise ValueError("App is not ready with the expected snapshot")
        report["remote_readiness"] = readiness
    for kind, text in FIXTURES:
        reference = encoder.encode(text, kind)
        times = []
        checks = []
        for _ in range(5):
            started = time.perf_counter()
            if args.url:
                response = session.post(
                    args.url.rstrip("/") + "/api/embed",
                    headers=auth.authenticate(),
                    json={"text": text, "kind": kind},
                    timeout=60,
                    allow_redirects=False,
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("version") != args.version:
                    raise ValueError("Remote model version changed")
                vector = payload["embedding"]
            else:
                vector = encoder.encode(text, kind)
            times.append(time.perf_counter() - started)
            checks.append(compare(reference, vector))
        report["results"].append(
            {
                "kind": kind,
                "characters": len(text),
                "median_seconds": statistics.median(times),
                "max_seconds": max(times),
                "compatibility": checks,
            }
        )
    if args.url:
        response = session.get(
            args.url.rstrip("/") + "/api/ready",
            headers=auth.authenticate(),
            timeout=30,
            allow_redirects=False,
        )
        response.raise_for_status()
        report["remote_after_inference"] = response.json()
    # Linux ru_maxrss is KiB. This measures the probe process, not the remote App.
    report["local_peak_rss_mib"] = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    )
    report["compatibility_passed"] = all(
        c["passed"] for r in report["results"] for c in r["compatibility"]
    )
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["compatibility_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

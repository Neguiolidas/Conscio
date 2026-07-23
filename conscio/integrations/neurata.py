"""NeurataBridge — CLI subprocess bridge to Neurata (extra opcional).

Conscio core never imports neurata. This bridge detects the binary via
shutil.which, queries via subprocess --json, and caches by context hash.
Without Neurata installed: available=False, everything returns None.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import time
from typing import Any

log = logging.getLogger("conscio.integrations.neurata")

_DEFAULT_TIMEOUT = 10.0
_DEFAULT_CACHE_TTL = 60.0


class NeurataBridge:
    """Thin CLI subprocess bridge to a Neurata installation.

    On construction, probes ``neurata doctor --json`` to discover the
    ``contract_version``.  If the binary is absent, or the contract
    version is not in ``contract_versions``, sets ``available = False``
    and every method returns ``None``.
    """

    def __init__(
        self,
        binary: str = "neurata",
        *,
        cache_ttl: float = _DEFAULT_CACHE_TTL,
        timeout: float = _DEFAULT_TIMEOUT,
        contract_versions: set[int] | None = None,
    ) -> None:
        self._binary = binary
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, Any]] = {}
        self._warned = False
        self._contract_versions = contract_versions
        self._contract: int | None = None
        self.available = False  # default; set True after successful probe

        if not shutil.which(binary):
            self.available = False
            return

        # Probe: bypass the available gate (we haven't decided yet)
        probe = self._run("doctor")
        if probe is None:
            self.available = False
            return
        try:
            probe_data = json.loads(probe.stdout)
        except (json.JSONDecodeError, TypeError):
            self.available = False
            return
        self._contract = probe_data.get("contract_version")
        if (self._contract_versions is not None
                and self._contract not in self._contract_versions):
            if not self._warned:
                log.warning("neurata contract_version %s mismatch",
                            self._contract)
                self._warned = True
            self.available = False
            return
        self.available = True

    # -- public API -----------------------------------------------------

    def query(self, q: str, limit: int = 5,
              context_hash: str | None = None) -> dict | None:
        args = ["query", q, "--limit", str(limit)]
        return self._cached("query", context_hash, args)

    def deposit(self, body: str, **meta: str) -> dict | None:
        args = ["deposit", body]
        for k, v in meta.items():
            args.extend([f"--{k}", str(v)])
        return self._run_json(*args)  # deposits are not cached

    def shelf_insights(self) -> dict | None:
        return self._run_json("shelf", "--insights")

    # -- internal -------------------------------------------------------

    def _cached(self, _label: str, context_hash: str | None,
                args: list[str]) -> dict | None:
        if not self.available:
            return None
        key = context_hash or hashlib.sha1(
            " ".join(args).encode()).hexdigest()[:12]
        now = time.time()
        if key in self._cache:
            ts, result = self._cache[key]
            if now - ts < self._cache_ttl:
                return result
        result = self._run_json(*args)
        if result is not None:
            self._cache[key] = (now, result)
        return result

    def _run_json(self, *args: str) -> dict | None:
        if not self.available:
            return None
        proc = self._run(*args)
        if proc is None:
            return None
        try:
            return json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            if not self._warned:
                log.warning("neurata returned non-JSON output")
                self._warned = True
            return None

    def _run(self, *args: str) -> subprocess.CompletedProcess | None:
        full_args = [self._binary, *args, "--json"]
        try:
            proc = subprocess.run(
                full_args,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        if proc.returncode != 0:
            return None
        return proc

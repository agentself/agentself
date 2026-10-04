"""Compare mail ref lookup with a supplied baseline module using temporary data.

Run: uv run python benchmarks/mail_ref_probe.py PATH_TO_BASELINE_MAIL_STATE.py
Reports three local wall-clock samples per case, counted mapping reads, and
exact parity of returned headers and saved mapping bytes. No provider calls.
"""

import importlib.util
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    from agentself.internal.mail_state import MailRefState

    spec = importlib.util.spec_from_file_location("baseline_mail_state", sys.argv[1])
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "python": sys.version,
                "temp": tempfile.gettempdir(),
            }
        ),
        flush=True,
    )
    for saved in (100, 2000):
        for kind in ("existing", "mixed"):
            results = {"before": [], "after": []}
            reference = None
            for repeat in range(3):
                for label, cls in (
                    ("before", baseline.MailRefState),
                    ("after", MailRefState),
                ):
                    with tempfile.TemporaryDirectory(prefix="mail-ref-bench-") as temp:
                        root = Path(temp)
                        folder = root / "identities" / "agent" / "email" / "refs"
                        folder.mkdir(parents=True)
                        for n in range(1, saved + 1):
                            (folder / f"m{n}").write_text(
                                f"provider/{n}", encoding="utf-8"
                            )
                        ids = (
                            [f"provider/{n}" for n in range(1, 101)]
                            if kind == "existing"
                            else [f"provider/{n}" for n in range(1, 51)]
                            + [f"new/{n}" for n in range(1, 26)] * 2
                        )
                        messages = [
                            {"id": item, "subject": f"Header {i}"}
                            for i, item in enumerate(ids)
                        ]
                        count = [0]
                        original = Path.read_text

                        def read(path, *args, **kwargs):
                            if path.parent == folder:
                                count[0] += 1
                            return original(path, *args, **kwargs)

                        with patch.object(Path, "read_text", read):
                            started = time.perf_counter()
                            result = cls(root).apply("agent", messages)
                            elapsed = time.perf_counter() - started
                        snapshot = (
                            result,
                            {p.name: p.read_bytes() for p in folder.iterdir()},
                        )
                        if reference is None:
                            reference = snapshot
                        assert snapshot == reference, "Output or on-disk parity failed"
                        results[label].append({"seconds": elapsed, "reads": count[0]})
            print(
                json.dumps(
                    {
                        "saved": saved,
                        "headers": 100,
                        "kind": kind,
                        "parity": True,
                        "results": results,
                        "medians": {
                            key: statistics.median(r["seconds"] for r in rows)
                            for key, rows in results.items()
                        },
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()

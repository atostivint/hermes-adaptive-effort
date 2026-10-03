"""Reject a green pytest run that skipped integration or collected no tests."""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree


def verify(path: Path) -> int:
    root = ElementTree.parse(path).getroot()
    cases = list(root.iter("testcase"))
    skipped = [case for case in cases if case.find("skipped") is not None]
    failed = [case for case in cases if case.find("failure") is not None
              or case.find("error") is not None]
    integration = [case for case in cases
                   if "test_dispatcher_integration" in case.get("classname", "")]
    if not cases or skipped or failed or len(integration) < 6:
        raise SystemExit(
            f"Incomplete tests: total={len(cases)}, skipped={len(skipped)}, "
            f"failed={len(failed)}, dispatcher={len(integration)} (need at least 6)"
        )
    print(f"Verified {len(cases)} passed tests, including {len(integration)} dispatcher tests")
    return len(cases)


if __name__ == "__main__":
    verify(Path(sys.argv[1]))

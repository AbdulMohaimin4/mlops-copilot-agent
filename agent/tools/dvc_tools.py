"""DVC tools: dataset inventory, versions, comparison, change detection and reproduction.

Every DVC/git invocation goes through `_run_allowlisted`, which only permits
read-only command prefixes, passes an argv list (never a shell), and times out.
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agent.config import load_settings
from agent.schemas import NoInput
from agent.tools.common import ActionRejectedError, ServiceUnavailableError, Tool

# Read-only command prefixes. `dvc get` only writes into the scratch directory.
ALLOWED_COMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "dvc": (("list",), ("status",), ("diff",), ("get",), ("params", "diff"), ("remote", "list")),
    "git": (("rev-parse",), ("describe",), ("log",), ("show",), ("tag", "--list")),
}
# A revision must not start with "-" so it can never be parsed as an option.
REV_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/~^-]{0,99}$"


class RevisionInput(BaseModel):
    rev: str = Field(
        pattern=REV_PATTERN, description="Git revision: a tag such as data-v1, a branch or a SHA."
    )


class CompareInput(BaseModel):
    rev_a: str = Field(pattern=REV_PATTERN, description="Older revision, e.g. data-v1.")
    rev_b: str = Field(
        default="HEAD", pattern=REV_PATTERN, description="Newer revision, e.g. data-v2."
    )


def _run_allowlisted(program: str, args: list[str]) -> str:
    """Run an allowlisted git/dvc command in the project directory and return stdout."""
    prefixes = ALLOWED_COMMANDS.get(program, ())
    if not any(tuple(args[: len(prefix)]) == prefix for prefix in prefixes):
        raise ActionRejectedError(f"command not allowlisted: {program} {' '.join(args[:2])}")
    settings = load_settings()
    executable = [sys.executable, "-m", "dvc"] if program == "dvc" else ["git"]
    try:
        completed = subprocess.run(
            [*executable, *args],
            cwd=settings.project_dir,
            capture_output=True,
            text=True,
            timeout=settings.tool_timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ServiceUnavailableError(f"{program} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceUnavailableError(f"{program} {args[0]} timed out") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"{program} {args[0]} failed: {completed.stderr.strip()[-500:]}")
    return completed.stdout


def _locked_outputs(rev: str) -> dict[str, str]:
    """Map each DVC output path to its md5 as recorded in dvc.lock at `rev`."""
    lock = yaml.safe_load(_run_allowlisted("git", ["show", f"{rev}:dvc.lock"]))
    return {
        out["path"]: out["md5"]
        for stage in lock["stages"].values()
        for out in stage.get("outs", [])
    }


def _nearest_data_tag() -> str | None:
    try:
        return _run_allowlisted(
            "git", ["describe", "--tags", "--match", "data-v*", "--abbrev=0"]
        ).strip()
    except RuntimeError:
        return None


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def list_tracked_datasets(_: NoInput) -> dict[str, Any]:
    """List the datasets tracked by DVC in this repository with path, size, md5 hash and the
    configured DVC remote."""
    entries = json.loads(_run_allowlisted("dvc", ["list", ".", "--dvc-only", "-R", "--json"]))
    datasets = [{"path": e["path"], "md5": e.get("md5"), "size_bytes": e["size"]} for e in entries]
    remotes = _run_allowlisted("dvc", ["remote", "list"]).split()
    return {"datasets": datasets, "remote": remotes[:2]}


def get_current_dataset_version(_: NoInput) -> dict[str, Any]:
    """Report the dataset version currently checked out: git commit, nearest data-v* tag and
    whether its data matches, per-file md5 from dvc.lock, the commit that last changed the data,
    and whether the workspace files match dvc.lock."""
    head_outputs = _locked_outputs("HEAD")
    tag = _nearest_data_tag()
    last_change = _run_allowlisted(
        "git", ["log", "-1", "--format=%H|%cI|%s", "--", "dvc.lock"]
    ).strip()
    commit, date, subject = last_change.split("|", 2)
    workspace_changes = json.loads(_run_allowlisted("dvc", ["status", "--json"]))
    return {
        "git_commit": _run_allowlisted("git", ["rev-parse", "HEAD"]).strip(),
        "data_version_tag": tag,
        "tag_matches_current_data": tag is not None and _locked_outputs(tag) == head_outputs,
        "files": head_outputs,
        "last_data_change": {"commit": commit, "date": date, "message": subject},
        "workspace_matches_lock": workspace_changes == {},
    }


def compare_dataset_versions(args: CompareInput) -> dict[str, Any]:
    """Compare two dataset versions (git tags such as data-v1 and data-v2, branches or commits):
    changed DVC files with md5 hashes on each side and changed DVC parameters."""
    file_diff = json.loads(
        _run_allowlisted("dvc", ["diff", "--json", "--show-hash", args.rev_a, args.rev_b])
    )
    param_diff = json.loads(
        _run_allowlisted("dvc", ["params", "diff", "--json", args.rev_a, args.rev_b])
    )
    old, new = _locked_outputs(args.rev_a), _locked_outputs(args.rev_b)
    return {
        "rev_a": args.rev_a,
        "rev_b": args.rev_b,
        "identical": old == new,
        "md5_rev_a": old,
        "md5_rev_b": new,
        "file_changes": file_diff,
        "param_changes": param_diff,
    }


def check_data_changes(_: NoInput) -> dict[str, Any]:
    """Detect whether DVC-tracked data or pipeline inputs changed in the workspace relative to
    dvc.lock, and whether the local DVC cache is in sync with the DVC remote."""
    workspace = json.loads(_run_allowlisted("dvc", ["status", "--json"]))
    remote = json.loads(_run_allowlisted("dvc", ["status", "--cloud", "--json"]))
    return {
        "workspace_clean": workspace == {},
        "workspace_changes": workspace,
        "remote_in_sync": remote == {},
        "remote_changes": remote,
    }


def reproduce_dataset_version(args: RevisionInput) -> dict[str, Any]:
    """Reproduce a previous dataset version without touching the workspace: fetch it with
    `dvc get` at the given git revision into a scratch directory, verify every file's md5 against
    the dvc.lock recorded at that revision, and return the steps to restore it in a checkout."""
    commit = _run_allowlisted("git", ["rev-parse", "--verify", f"{args.rev}^{{commit}}"]).strip()
    scratch = load_settings().scratch_dir / re.sub(r"[^A-Za-z0-9._-]", "_", args.rev)
    files = []
    for path, expected in _locked_outputs(commit).items():
        target = (scratch / path).resolve()
        if not target.is_relative_to(scratch):
            raise ActionRejectedError(f"refusing to write outside scratch dir: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _run_allowlisted("dvc", ["get", ".", path, "--rev", commit, "-o", str(target), "--force"])
        actual = _md5(target)
        files.append(
            {
                "path": path,
                "reproduced_to": str(target),
                "expected_md5": expected,
                "actual_md5": actual,
                "verified": actual == expected,
            }
        )
    return {
        "rev": args.rev,
        "git_commit": commit,
        "files": files,
        "all_verified": all(f["verified"] for f in files),
        "restore_steps": [
            f"git checkout {args.rev}  # or: git worktree add ../repro-{args.rev} {args.rev}",
            "dvc pull  # fetch the exact files recorded in dvc.lock from the DVC remote",
            "dvc status  # must report no changes",
            "dvc repro  # optional: rebuild from params.yaml + code; same md5 expected",
        ],
    }


TOOLS = [
    Tool.from_function(func)
    for func in (
        list_tracked_datasets,
        get_current_dataset_version,
        compare_dataset_versions,
        check_data_changes,
        reproduce_dataset_version,
    )
]

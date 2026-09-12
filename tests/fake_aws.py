#!/usr/bin/env python3
import fnmatch
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path


def option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def etag(path: Path) -> str:
    return f'"{hashlib.md5(path.read_bytes()).hexdigest()}"'


def error(code: str, operation: str) -> int:
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    print(
        f"An error occurred ({code}) when calling {operation}: "
        f"{secret} fake-private-body",
        file=sys.stderr,
    )
    return 255


def selected(relative: str, argv: list[str]) -> bool:
    allowed = "--exclude" not in argv
    index = 0
    while index < len(argv):
        if argv[index] in {"--exclude", "--include"}:
            if fnmatch.fnmatch(relative, argv[index + 1]):
                allowed = argv[index] == "--include"
            index += 2
        else:
            index += 1
    return allowed


def r2_path(root: Path, url: str) -> Path:
    bucket_and_key = url.removeprefix("s3://")
    _, _, key = bucket_and_key.partition("/")
    return root / key


def sync(argv: list[str], root: Path) -> int:
    if "--no-follow-symlinks" not in argv:
        return error("InvalidArgument", "Sync")
    source_value = argv[2]
    destination_value = argv[3]
    if source_value.startswith("s3://"):
        source = r2_path(root, source_value)
        destination = Path(destination_value)
    else:
        source = Path(source_value)
        destination = r2_path(root, destination_value)
    if not source.exists():
        return 0
    for path in source.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source).as_posix()
        if not selected(relative, argv):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return 0


def log_call(argv: list[str]) -> None:
    log = os.environ.get("FAKE_AWS_LOG")
    if not log:
        return
    with Path(log).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(argv) + "\n")


arguments = sys.argv[1:]
if arguments == ["--version"]:
    print("aws-cli/2.27.20 Python/3.12 fake")
    raise SystemExit(0)

log_call(arguments)
root = Path(os.environ["FAKE_R2_ROOT"])
service_index = next(
    (index for index, value in enumerate(arguments) if value in {"s3api", "s3"}),
    None,
)
if service_index is None:
    raise SystemExit(255)
command = arguments[service_index:]

forced_failure = os.environ.get("FAKE_AWS_FAIL_OPERATION")
if forced_failure == command[1]:
    if command[1] == "get-object":
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("partial-private", encoding="utf-8")
    raise SystemExit(error("AccessDenied", command[1]))

if command[:2] == ["s3api", "head-object"]:
    target = root / option(command, "--key")
    if not target.is_file():
        raise SystemExit(error("NoSuchKey", "HeadObject"))
    print(json.dumps({"ETag": etag(target)}))
    raise SystemExit(0)

if command[:2] == ["s3api", "get-object"]:
    target = root / option(command, "--key")
    if not target.is_file():
        raise SystemExit(error("NoSuchKey", "GetObject"))
    if option(command, "--if-match") != etag(target):
        raise SystemExit(error("PreconditionFailed", "GetObject"))
    output = Path(command[-1])
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, output)
    print("{}")
    raise SystemExit(0)

if command[:2] == ["s3api", "put-object"]:
    target = root / option(command, "--key")
    if "--if-none-match" in command:
        if option(command, "--if-none-match") != "*" or target.exists():
            raise SystemExit(error("PreconditionFailed", "PutObject"))
    elif "--if-match" in command:
        if not target.is_file():
            raise SystemExit(error("NoSuchKey", "PutObject"))
        if option(command, "--if-match") != etag(target):
            raise SystemExit(error("PreconditionFailed", "PutObject"))
    else:
        raise SystemExit(error("InvalidArgument", "PutObject"))
    source = Path(option(command, "--body"))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(json.dumps({"ETag": etag(target)}))
    raise SystemExit(0)

if command[:2] == ["s3", "sync"]:
    raise SystemExit(sync(command, root))

raise SystemExit(255)

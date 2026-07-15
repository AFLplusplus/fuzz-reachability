import os
import tempfile
from dataclasses import dataclass


class OutputError(RuntimeError):
    pass


@dataclass(frozen=True)
class OutputPaths:
    json: str
    reached: str
    not_reached: str
    dot: str | None

    def items(self):
        values = [
            ("--out", self.json),
            ("--reached", self.reached),
            ("--not-reached", self.not_reached),
        ]
        if self.dot is not None:
            values.append(("--dot", self.dot))
        return values


def _destination(option, value):
    path = os.path.abspath(os.path.expanduser(os.fspath(value)))
    if os.path.lexists(path) and not (os.path.isfile(path) or os.path.islink(path)):
        raise OutputError(f"{option} must name a file, not a directory: {path}")
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        raise OutputError(f"parent directory for {option} does not exist: {parent}")
    return path


def resolve(args):
    json_path = args.out or os.path.join(args.project, "reachability.json")
    json_path = _destination("--out", json_path)
    outdir = os.path.dirname(json_path)
    reached = _destination(
        "--reached", args.reached or os.path.join(outdir, "reached.txt")
    )
    not_reached = _destination(
        "--not-reached",
        args.not_reached or os.path.join(outdir, "not_reached.txt"),
    )
    dot = _destination("--dot", args.dot) if args.dot else None
    result = OutputPaths(json_path, reached, not_reached, dot)
    seen = {}
    for option, path in result.items():
        identity = os.path.normcase(os.path.realpath(path))
        previous = seen.get(identity)
        if previous:
            raise OutputError(
                f"output destinations collide after path resolution: {previous} and {option}"
            )
        seen[identity] = option
    return result


class Transaction:
    def __init__(self, paths):
        self.paths = paths
        self.staged = {}
        self.backups = {}
        self.published = []

    def __enter__(self):
        try:
            for option, final in self.paths.items():
                fd, stage = tempfile.mkstemp(
                    prefix=f".{os.path.basename(final)}.reachability-",
                    dir=os.path.dirname(final),
                )
                os.close(fd)
                self.staged[option] = stage
        except OSError as exc:
            self.close()
            raise OutputError(f"cannot stage output files: {exc}") from exc
        return self

    def path(self, option):
        return self.staged[option]

    def publish(self):
        try:
            missing = [
                option for option, path in self.staged.items()
                if not os.path.isfile(path) or os.path.getsize(path) == 0
            ]
        except OSError as exc:
            raise OutputError(f"cannot validate staged output files: {exc}") from exc
        if missing:
            raise OutputError("output stage did not produce: " + ", ".join(missing))
        try:
            for option, final in self.paths.items():
                if os.path.lexists(final):
                    fd, backup = tempfile.mkstemp(
                        prefix=f".{os.path.basename(final)}.backup-",
                        dir=os.path.dirname(final),
                    )
                    os.close(fd)
                    os.unlink(backup)
                    os.replace(final, backup)
                    self.backups[option] = backup
            for option, final in self.paths.items():
                os.replace(self.staged[option], final)
                self.published.append(option)
        except OSError as exc:
            for option in reversed(self.published):
                final = dict(self.paths.items())[option]
                if os.path.lexists(final):
                    os.unlink(final)
            for option, backup in self.backups.items():
                final = dict(self.paths.items())[option]
                if os.path.lexists(backup):
                    os.replace(backup, final)
            self.published.clear()
            self.backups.clear()
            raise OutputError(f"cannot publish output files atomically: {exc}") from exc
        self.staged.clear()
        self.published.clear()
        for backup in self.backups.values():
            try:
                if os.path.lexists(backup):
                    os.unlink(backup)
            except OSError:
                pass
        self.backups.clear()

    def close(self):
        for path in self.staged.values():
            try:
                if os.path.lexists(path):
                    os.unlink(path)
            except OSError:
                pass
        for path in self.backups.values():
            try:
                if os.path.lexists(path):
                    os.unlink(path)
            except OSError:
                pass
        self.staged.clear()
        self.backups.clear()

    def __exit__(self, exc_type, exc, tb):
        self.close()

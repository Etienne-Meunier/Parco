"""Error hierarchy of parco. Every message is meant to be shown to the user or the AI."""


class ParcoError(Exception):
    """Base class of every error raised by parco core."""


class NotInitializedError(ParcoError):
    """No `.parco` folder was found in the current directory or its parents."""


class AlreadyInitializedError(ParcoError):
    """A `.parco` folder already exists where `init` was asked to create one."""


class ConfigError(ParcoError):
    """The configuration is missing, malformed or points to missing files."""


class PhaseError(ParcoError):
    """The operation is not allowed in the current phase."""


class RuleError(ParcoError):
    """The operation is refused by the rules of the current mode or phase."""


class AnchorError(ParcoError):
    """An anchor does not match exactly once in its file."""


class OverlapError(ParcoError):
    """An edit overlaps another non-rejected edit of the same file."""


class NotFoundError(ParcoError):
    """An edit or thread id does not exist in the current version."""


class InputError(ParcoError):
    """The JSON input of a command is malformed or misses required fields."""


class FilesModifiedError(ParcoError):
    """Tracked files changed since the version was opened, the review is frozen."""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        super().__init__(
            "Tracked files were modified since the version was opened: "
            + ", ".join(files)
            + ". The review cannot continue: run `parco close` to end this version, "
            "then `parco open` to start a new one."
        )

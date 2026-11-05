# Workspace files the server reads into its own memory, far above real files: a fork can sync in anything
MAX_READ_SIZE = 32 << 20


class FileTooLarge(ValueError):
    pass


def check_read_size(size: int, name: str) -> None:
    if size > MAX_READ_SIZE:
        raise FileTooLarge(f"{name} is larger than {MAX_READ_SIZE >> 20} MB")

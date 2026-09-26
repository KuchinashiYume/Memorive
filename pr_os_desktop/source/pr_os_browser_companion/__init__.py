from pathlib import Path


def extension_root() -> Path:
    return (Path(__file__).resolve().parent / "extension").resolve()


__all__ = ["extension_root"]

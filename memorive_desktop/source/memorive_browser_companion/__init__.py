from pathlib import Path


def extension_root() -> Path:
    return (Path(__file__).resolve().parent / "extension").resolve()


__all__ = []

# Names the package offered before; restored with the code behind them.
__all__ = [*__all__, 'extension_root']

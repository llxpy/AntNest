"""Console entry point for `uvx antnest` / the `antnest` command.

For packaged/distributed runs (uvx, installer) user data must go to a
writable location (%LOCALAPPDATA%/AntNest), not inside the possibly
read-only install directory. Setting ANT_INSTALLED=1 triggers that path
in prototype_antnest.py without affecting the in-repo dev workflow
(`uv run python prototype_antnest.py` leaves it unset, keeping config local).
"""
import os


def run() -> None:
    os.environ.setdefault("ANT_INSTALLED", "1")
    import prototype_antnest  # noqa: E402  (import after env is set)
    prototype_antnest.app.run()


if __name__ == "__main__":
    run()

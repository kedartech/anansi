"""Command-line entry point."""

import typer

from anansi import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Anansi game engine."""


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(__version__)

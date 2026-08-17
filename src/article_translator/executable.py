from __future__ import annotations

import sys


def main() -> None:
    """Launch the workbench by default while retaining the complete CLI."""

    if len(sys.argv) == 1:
        from article_translator.cli import launch_app

        launch_app()
        return

    from article_translator.cli import app

    app(prog_name="ArticleTranslator")


if __name__ == "__main__":
    main()

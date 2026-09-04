def main() -> None:
    # Imported lazily so importing the package (e.g. in tests) doesn't require env vars.
    from leaf_valley.bot import run

    run()

import argparse

from cli import new_high


def test_daily_default_does_not_push():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    new_high.register_subparser(subparsers)

    args = parser.parse_args(["new-high", "daily"])

    assert args.command == "new-high"
    assert args.new_high_command == "daily"
    assert args.push is False
    assert args.top_n == 10

from dataclasses import dataclass

from argklass import ArgumentParser, argument


@dataclass
class DashArgs:
    use_dash: str = "default"
    multi_word_option: int = 42
    no_underscores: bool = False


@dataclass
class DashArgsExplicit:
    use_dash: str = argument(default="default", help="an option with dashes")
    flag_option: bool = argument(action="store_true", help="a boolean flag")


def test_underscore_still_works():
    parser = ArgumentParser(dataclass=DashArgs)
    args = parser.parse_args(["--use_dash", "hello", "--multi_word_option", "7"])
    assert args.use_dash == "hello"
    assert args.multi_word_option == 7


def test_dash_variant_works():
    parser = ArgumentParser(dataclass=DashArgs)
    args = parser.parse_args(["--use-dash", "hello", "--multi-word-option", "7"])
    assert args.use_dash == "hello"
    assert args.multi_word_option == 7


def test_mixed_dash_and_underscore():
    parser = ArgumentParser(dataclass=DashArgs)
    args = parser.parse_args(["--use-dash", "hello", "--multi_word_option", "7"])
    assert args.use_dash == "hello"
    assert args.multi_word_option == 7


def test_bool_flag_with_dashes():
    parser = ArgumentParser(dataclass=DashArgs)
    args = parser.parse_args(["--no-underscores"])
    assert args.no_underscores is True


def test_explicit_argument_with_dashes():
    parser = ArgumentParser(dataclass=DashArgsExplicit)
    args = parser.parse_args(["--use-dash", "value", "--flag-option"])
    assert args.use_dash == "value"
    assert args.flag_option is True


def test_explicit_argument_with_underscores():
    parser = ArgumentParser(dataclass=DashArgsExplicit)
    args = parser.parse_args(["--use_dash", "value", "--flag_option"])
    assert args.use_dash == "value"
    assert args.flag_option is True


def test_grouped_dash_variant():
    @dataclass
    class Inner:
        inner_opt: str = "x"

    @dataclass
    class Outer:
        outer_opt: int = 1

    parser = ArgumentParser(dataclass=Outer)
    args = parser.parse_args(["--outer-opt", "5"])
    assert args.outer_opt == 5


def test_no_dash_in_name_unchanged():
    @dataclass
    class Simple:
        verbose: bool = False
        count: int = 0

    parser = ArgumentParser(dataclass=Simple)
    args = parser.parse_args(["--verbose", "--count", "3"])
    assert args.verbose is True
    assert args.count == 3

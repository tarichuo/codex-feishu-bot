from __future__ import annotations

from feishu_codex_bot.app import build_parser, main


def test_build_parser_accepts_dump_flag() -> None:
    args = build_parser().parse_args(["--dump"])

    assert args.dump is True


def test_main_passes_dump_flag_to_runtime(monkeypatch) -> None:
    captured: list[bool] = []

    def _fake_run(*, enable_dump: bool = False) -> int:
        captured.append(enable_dump)
        return 0

    monkeypatch.setattr("feishu_codex_bot.app.run", _fake_run)

    result = main(["--dump"])

    assert result == 0
    assert captured == [True]

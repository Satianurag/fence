from fence.resolve import refs_from, strip_trailers


def test_file_with_line_range():
    files, _ = refs_from("see cmd/entire/cli/trailers/trailers.go:120-134 and attach.go#L10-L12")
    assert files == [("cmd/entire/cli/trailers/trailers.go", 120, 134), ("attach.go", 10, 12)]


def test_file_without_line_and_dedup():
    files, _ = refs_from("trailers.go is wrong. trailers.go again.")
    assert files == [("trailers.go", None, None)]


def test_symbols_backticked_and_called():
    _, syms = refs_from("`AppendCheckpointTrailer` breaks; also parseFinalBlock( and `trailers.FormatStrategy()`")
    assert syms == ["AppendCheckpointTrailer", "parseFinalBlock", "FormatStrategy"]


def test_stop_words_and_short_symbols_dropped():
    _, syms = refs_from("`Config` `Run` `ab` `Hooks`")
    assert syms == []


def test_no_refs_in_plain_text():
    assert refs_from("it deletes unrelated keys") == ([], [])
    assert refs_from(None) == ([], [])


def test_strip_trailers():
    body = "Why we did it.\n\nEntire-Checkpoint: 475f14207459\nCo-Authored-By: X <x@y>\nSigned-off-by: Z"
    assert strip_trailers(body) == "Why we did it."

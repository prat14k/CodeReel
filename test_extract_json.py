"""The shapes local models actually return. Run: python test_extract_json.py"""
import pathlib
import tempfile

import providers
from providers import extract_json as x

def main():
    # clean
    assert x('{"a":1}') == {"a": 1}
    # fenced, with leading whitespace
    assert x('\n\n```json\n{"a":1}\n```') == {"a": 1}
    # prose either side
    assert x('Here you go:\n{"a":1}\nHope that helps!') == {"a": 1}
    # reasoning block, closed and unclosed
    assert x('<think>maybe {"b":2}</think>{"a":1}') == {"a": 1}
    assert x('I should emit {"b":2} first.</think>{"a":1}') == {"a": 1}
    # raw newline inside a string value
    assert x('{"t":"one\ntwo"}') == {"t": "one\ntwo"}
    # trailing comma, and a comment next to a URL that must survive
    assert x('{"a":1, "u":"https://x.dev", // note\n}') == {"a": 1, "u": "https://x.dev"}
    # truncated mid-string and mid-array (hit max_tokens)
    assert x('{"scenes":[{"text":"hi"},{"text":"half') == \
        {"scenes": [{"text": "hi"}, {"text": "half"}]}
    # truncated right after a key
    assert x('{"a":1,"b":') == {"a": 1, "b": ""}
    # no JSON at all -> a readable error, not a crash
    for bad in ("", "I cannot help with that."):
        try:
            x(bad)
            raise AssertionError("expected ProviderError")
        except providers.ProviderError:
            pass
    # the hook must not be said again as a scene, or as the close
    with tempfile.TemporaryDirectory() as td:
        r = providers.normalise({
            "title": "T", "hook": "Your Mac's notch is wasted.",
            "scenes": [{"narration": "Your Mac's notch is wasted!", "role": "Hook"},
                       {"narration": "Every switch costs you flow.", "role": "Problem"},
                       {"narration": "Grab it on GitHub.", "role": "Get it"}],
            "close": {"narration": "Grab it on GitHub.", "cta": "github.com/x"},
        }, pathlib.Path(td), None, providers.Provider())
    assert [sc["text"] for sc in r["scenes"]] == ["Every switch costs you flow."], r["scenes"]
    assert r["hook"] == "Your Mac's notch is wasted."
    assert r["close"]["text"] == "Grab it on GitHub."

    print("ok")

if __name__ == "__main__":
    main()

from ears.hooks import on_partial_utterance, on_utterance_final


def run_typed_loop() -> None:
    print("Chief — typed input mode. Empty line to exit.")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        on_partial_utterance(line)
        on_utterance_final(line)

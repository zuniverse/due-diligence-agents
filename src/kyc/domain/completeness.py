REQUIRED_DOCUMENTS = ("kbis", "statuts", "piece_identite")


def missing_documents(received: list[str]) -> list[str]:
    have = {d.casefold() for d in received}
    return [d for d in REQUIRED_DOCUMENTS if d not in have]

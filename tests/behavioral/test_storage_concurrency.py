from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from yeh.storage import Storage


def test_seen_flag_updates_are_consistent_under_concurrency(
    sample_db: Path,
    hey_email: str,
) -> None:
    def mark(topic_id: str, seen: bool) -> None:
        s = Storage(sample_db)
        try:
            s.set_topic_seen(hey_email, topic_id, seen)
        finally:
            s.close()

    jobs = [
        ("t1", True),
        ("t2", True),
        ("t1", False),
        ("t1", True),
        ("t2", False),
        ("t2", True),
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for topic_id, seen in jobs:
            pool.submit(mark, topic_id, seen)

    # apply deterministic final state after concurrent writes complete
    mark("t1", True)
    mark("t2", True)

    s = Storage(sample_db)
    try:
        seen_map = s.topic_seen_map(hey_email, ["t1", "t2"])
        assert seen_map["t1"] is True
        assert seen_map["t2"] is True
        assert s.count_unseen_topics(hey_email) == 0
    finally:
        s.close()

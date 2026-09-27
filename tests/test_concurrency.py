from pathlib import Path

from tpby.repository import Repository


def test_code_jobs_survive_restart_and_remain_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    repository = Repository(path)
    assert repository.enqueue_code_job(-1001, 10, "111111")
    assert not repository.enqueue_code_job(-1001, 10, "111111")
    assert repository.claim_code_job()["code"] == "111111"
    repository.close()

    repository = Repository(path)
    repository.reset_running_code_jobs()
    recovered = repository.claim_code_job()
    assert (recovered["code_chat_id"], recovered["code_message_id"]) == (-1001, 10)

    repository.record_failure(-1001, 10, "rejected")
    repository.complete_code_job(-1001, 10)
    assert repository.claim_code_job() is None
    assert not repository.enqueue_code_job(-1001, 10, "111111")
    repository.close()

from dam import queue
from dam.run import embedded_worker_commands
from dam.worker import AI_QUEUES, CORE_QUEUES


def _queues(cmd):
    return cmd[cmd.index("--queues") + 1].split(",")


def _concurrency(cmd):
    return int(cmd[cmd.index("--concurrency") + 1])


def test_app_runs_separate_core_and_ai_pools_by_default():
    cmds = embedded_worker_commands({})
    assert set(cmds) == {"worker-core", "worker-ai"}
    assert _queues(cmds["worker-core"]) == ["ingest", "scan", "render"]
    assert _queues(cmds["worker-ai"]) == ["embed", "vision", "speech"]
    assert not set(CORE_QUEUES) & set(AI_QUEUES)


def test_pool_sizes_are_configurable_and_zero_disables():
    cmds = embedded_worker_commands({"EMBEDDED_CORE_CONCURRENCY": "6", "EMBEDDED_AI_CONCURRENCY": "0"})
    assert set(cmds) == {"worker-core"} and _concurrency(cmds["worker-core"]) == 6
    assert embedded_worker_commands({"EMBEDDED_WORKER_CONCURRENCY": "0"}) == {}
    # Blank values (compose passes `${VAR:-}` through as "") fall back to the defaults.
    cmds = embedded_worker_commands({"EMBEDDED_WORKER_CONCURRENCY": "", "EMBEDDED_CORE_CONCURRENCY": ""})
    assert _concurrency(cmds["worker-core"]) == 3


def test_core_pool_takes_ingest_even_when_ai_jobs_outrank_it(session):
    for _ in range(5):
        queue.enqueue(session, "describe", queue="vision", priority=100)
    queue.enqueue(session, "ingest", queue="ingest", priority=10)
    session.commit()
    job = queue.claim(session, CORE_QUEUES, "core-1")
    assert job is not None and job.kind == "ingest"

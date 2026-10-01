"""Tests for LogStore and LogStoreHandler."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from vajsave.log_store import (
    LogRecordEntry,
    LogStore,
    LogStoreHandler,
    get_log_store,
    setup_logging,
)


def test_log_store_basic_add_and_get():
    store = LogStore(max_records=10)
    entry1 = LogRecordEntry(
        timestamp=1000.0,
        formatted_time="10:00:00",
        level="INFO",
        category="scan",
        message="开始扫描设备",
        logger_name="vajsave.scanner",
    )
    entry2 = LogRecordEntry(
        timestamp=1001.0,
        formatted_time="10:00:01",
        level="INFO",
        category="cover",
        message="正在获取游戏封面",
        logger_name="vajsave.artwork.service",
    )
    entry3 = LogRecordEntry(
        timestamp=1002.0,
        formatted_time="10:00:02",
        level="WARNING",
        category="app",
        message="应用常规提示",
        logger_name="vajsave.app",
    )

    store.add_record(entry1)
    store.add_record(entry2)
    store.add_record(entry3)

    assert len(store.get_records()) == 3
    assert len(store.get_records(category="scan")) == 1
    assert store.get_records(category="scan")[0].message == "开始扫描设备"
    assert len(store.get_records(category="cover")) == 1
    assert store.get_records(category="cover")[0].message == "正在获取游戏封面"

    # Search filter
    search_res = store.get_records(search="封面")
    assert len(search_res) == 1
    assert search_res[0].category == "cover"

    # Clear
    store.clear()
    assert len(store.get_records()) == 0


def test_log_store_maxlen():
    store = LogStore(max_records=3)
    for i in range(5):
        store.add_record(
            LogRecordEntry(
                timestamp=float(i),
                formatted_time=f"10:00:0{i}",
                level="INFO",
                category="scan",
                message=f"记录 {i}",
                logger_name="vajsave.scanner",
            )
        )
    records = store.get_records()
    assert len(records) == 3
    assert [r.message for r in records] == ["记录 2", "记录 3", "记录 4"]


def test_log_store_subscription():
    store = LogStore()
    received = []

    unsub = store.subscribe(lambda entry: received.append(entry))

    e1 = LogRecordEntry(1.0, "10:00:01", "INFO", "scan", "test1", "vajsave.scanner")
    store.add_record(e1)
    assert len(received) == 1
    assert received[0] == e1

    unsub()
    e2 = LogRecordEntry(2.0, "10:00:02", "INFO", "scan", "test2", "vajsave.scanner")
    store.add_record(e2)
    assert len(received) == 1  # No new entries received after unsub


def test_log_store_handler_categorization():
    store = LogStore()
    handler = LogStoreHandler(store=store)
    logger = logging.getLogger("test_vajsave_cat")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    scan_logger = logging.getLogger("vajsave.scanner")
    scan_logger.setLevel(logging.DEBUG)
    scan_logger.addHandler(handler)

    cover_logger = logging.getLogger("vajsave.artwork.service")
    cover_logger.setLevel(logging.DEBUG)
    cover_logger.addHandler(handler)

    scan_logger.info("扫描日志测试")
    cover_logger.info("封面日志测试")
    logger.info("普通应用日志")

    scan_records = store.get_records(category="scan")
    assert any("扫描日志测试" in r.message for r in scan_records)

    cover_records = store.get_records(category="cover")
    assert any("封面日志测试" in r.message for r in cover_records)

    app_records = store.get_records(category="app")
    assert any("普通应用日志" in r.message for r in app_records)


def test_setup_logging_idempotence():
    handler1 = setup_logging()
    handler2 = setup_logging()
    assert handler1 is handler2


def test_multithreaded_logging():
    store = LogStore(max_records=500)
    handler = LogStoreHandler(store=store)
    logger = logging.getLogger("vajsave.threaded_test")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    def worker(idx):
        for j in range(20):
            logger.info("Worker %d message %d", idx, j)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(worker, i) for i in range(5)]
        for f in futures:
            f.result()

    assert len(store.get_records()) == 100

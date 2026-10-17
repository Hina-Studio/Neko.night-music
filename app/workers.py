# -*- coding: utf-8 -*-
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot


class WorkerSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)


class ProgressBridge(QObject):
    progress = pyqtSignal(object)


class Worker(QRunnable):
    def __init__(self, fn, *args, cancel=None, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self._cancel = cancel
        self.signals = WorkerSignals()
        self.setAutoDelete(False)

    def with_progress(self):
        last_percent = [-1]

        def callback(received, total):
            if _shutting_down or (self._cancel is not None and self._cancel()):
                return False
            if total > 0:
                percent = int(received * 100 / total)
                if percent == last_percent[0]:
                    return True
                last_percent[0] = percent
            try:
                self.signals.progress.emit(int(received), int(total))
            except RuntimeError:
                return False
            return True

        self.kwargs["progress"] = callback
        return self

    @pyqtSlot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001
            if not _shutting_down:
                try:
                    self.signals.failed.emit(str(exc))
                except RuntimeError:
                    pass
            return
        if not _shutting_down:
            try:
                self.signals.finished.emit(result)
            except RuntimeError:
                pass


_pool = None
_download_pool = None
_active_workers = set()
_shutting_down = False


def get_pool():
    global _pool
    if _pool is None:
        _pool = QThreadPool.globalInstance()
        _pool.setMaxThreadCount(max(4, _pool.maxThreadCount()))
    return _pool


def get_download_pool(max_threads=None):
    global _download_pool
    if _download_pool is None:
        _download_pool = QThreadPool()
        _download_pool.setMaxThreadCount(3)
    if max_threads:
        _download_pool.setMaxThreadCount(max(1, min(16, int(max_threads))))
    return _download_pool


def _start(worker, on_done, on_error, on_progress, pool):
    if on_progress is not None:
        worker.with_progress()
        worker.signals.progress.connect(on_progress)
    if on_done is not None:
        worker.signals.finished.connect(on_done)
    if on_error is not None:
        worker.signals.failed.connect(on_error)
    _active_workers.add(worker)
    worker.signals.finished.connect(
        lambda _result: _active_workers.discard(worker)
    )
    worker.signals.failed.connect(
        lambda _message: _active_workers.discard(worker)
    )
    pool.start(worker)
    return worker


def run_async(fn, *args, on_done=None, on_error=None, on_progress=None, **kwargs):
    worker = Worker(fn, *args, **kwargs)
    return _start(worker, on_done, on_error, on_progress, get_pool())


def run_download(fn, *args, on_done=None, on_error=None, on_progress=None,
                 cancel=None, **kwargs):
    worker = Worker(fn, *args, cancel=cancel, **kwargs)
    return _start(
        worker, on_done, on_error, on_progress, get_download_pool()
    )


def shutdown(timeout_ms=2000):
    global _shutting_down
    _shutting_down = True
    for worker in list(_active_workers):
        try:
            worker.signals.blockSignals(True)
        except RuntimeError:
            pass
    for pool in (_pool, _download_pool):
        if pool is None:
            continue
        try:
            pool.waitForDone(timeout_ms)
        except Exception:  # noqa: BLE001
            pass

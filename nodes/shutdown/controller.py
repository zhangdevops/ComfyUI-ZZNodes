from collections import deque
import logging
import math
import os
from pathlib import Path
import secrets
import threading
import time
from .windows_power import shutdown_windows

IDLE_SECONDS = 30


class MemoryLogHandler(logging.Handler):
    """Only retain the latest 100 records in this process; never write to a stream."""
    def __init__(self):
        super().__init__()
        self.records = deque(maxlen=100)
        self.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S'))

    def emit(self, record):
        # Handler.handle holds the same lock used by snapshot.
        self.records.append(self.format(record))

    def snapshot(self):
        with self.lock:
            return list(self.records)


# An isolated logger keeps page logs out of ComfyUI/launcher file and console handlers.
logger = logging.Logger(__name__, level=logging.INFO)
logger.propagate = False
_handler = MemoryLogHandler()
logger.addHandler(_handler)


class Controller:
    def __init__(self, queue_getter):
        self.queue_getter = queue_getter
        self.lock = threading.RLock()
        self.revision = 0
        self.token = ''
        self.state = 'disabled'
        self.message = '未启用；请主动打开“启用自动关机”'
        self.remaining = 0
        self.idle_since = None
        self.history_start = set()
        self.stop_file = Path(__file__).resolve().parents[2] / 'STOP_SHUTDOWN.txt'
        logger.info('自动关机控制已加载，尚未启用')

    def snapshot(self):
        with self.lock:
            return dict(revision=self.revision, arm_token=self.token, state=self.state,
                        message=self.message, remaining=self.remaining, enabled=bool(self.token),
                        logs=_handler.snapshot())

    def arm(self, revision):
        with self.lock:
            if revision != self.revision:
                raise ValueError('状态已经变化，请刷新后重新开启')
            if self.stop_file.exists():
                raise ValueError('请先删除 STOP_SHUTDOWN.txt')
            if os.name != 'nt':
                raise ValueError('真实关机只支持 Windows')
            if self.state == 'committing':
                raise ValueError('关机已提交，无法重新启用')
            self.history_start = set(self.queue_getter().get_history())
            self.revision += 1
            self.token = secrets.token_urlsafe(32)
            self.state, self.message = 'armed', '已准备；请提交包含本节点的工作流'
            self.remaining, self.idle_since = 0, None
            logger.info('已启用自动关机；队列持续空闲 %s 秒后直接关机', IDLE_SECONDS)

    def cancel(self, reason):
        with self.lock:
            if self.state == 'committing':
                self.message = 'Windows 关机已提交；无法保证撤回'
                logger.info('%s', self.message)
                return
            self.revision += 1
            self.token = ''
            self.state, self.message, self.remaining = 'cancelled', reason, 0
            logger.info('%s', reason)

    def submitted(self):
        with self.lock:
            if self.token and self.state == 'idle':
                logger.info('收到新任务提交，重新等待队列结束')
                self.state, self.message, self.remaining = 'waiting', '收到新任务，等待队列结束', 0
            self.idle_since = None

    def activate(self, token):
        with self.lock:
            valid = bool(token) and isinstance(token, str) and isinstance(self.token, str) and secrets.compare_digest(token, self.token)
            if not valid or self.state != 'armed':
                logger.warning('未启动监控：%s', '凭证缺失或无效' if not valid else '当前不是待启动状态（可能已启动或已取消）')
                return
            self.state, self.message = 'waiting', '等待全部排队及运行任务结束'
            logger.info('工作流已执行，开始监控全部排队及运行任务')
            revision = self.revision
            threading.Thread(target=self._monitor, args=(revision,), daemon=True, name='QueueShutdown').start()

    def failed_job(self, queue):
        for prompt_id, entry in queue.get_history().items():
            status = entry.get('status') or {}
            if prompt_id not in self.history_start and (status.get('status_str') != 'success' or status.get('completed') is False):
                # Do not dump the prompt/history: they may contain credentials.
                for item in status.get('messages', []):
                    if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], dict):
                        details = {k: item[1][k] for k in ('node_id', 'node_type', 'exception_type', 'exception_message', 'traceback') if k in item[1]}
                        logger.error('任务异常详情：事件=%s；%s', item[0], details)
                logger.error('检测到任务失败/中断；status_str=%s；completed=%s', status.get('status_str'), status.get('completed'))
                return True
        return False

    def _tick(self, revision, now):
        with self.lock:
            if revision != self.revision or not self.token:
                return False
            if self.stop_file.exists():
                self.cancel('已由 STOP_SHUTDOWN.txt 取消')
                return False
            queue = self.queue_getter()
            if self.failed_job(queue):
                self.cancel('检测到任务失败或中断，已取消关机')
                return False
            if queue.get_tasks_remaining():
                if self.state != 'waiting':
                    logger.info('检测到运行或排队任务，重新等待队列结束')
                self.idle_since = None
                self.state, self.message, self.remaining = 'waiting', '等待全部排队及运行任务结束', 0
                return True
            if self.idle_since is None:
                self.idle_since = now
                logger.info('队列已清空，开始空闲确认：%s 秒', IDLE_SECONDS)
            elapsed = now - self.idle_since
            if elapsed < IDLE_SECONDS:
                self.state, self.remaining = 'idle', math.ceil(IDLE_SECONDS - elapsed)
                self.message = f'队列空闲确认剩余 {self.remaining} 秒；关闭“启用自动关机”可取消'
                return True
            with queue.mutex:
                if queue.get_tasks_remaining():
                    self.idle_since = None
                    self.state, self.message, self.remaining = 'waiting', '等待全部排队及运行任务结束', 0
                    return True
                if self.failed_job(queue):
                    self.cancel('检测到任务失败或中断，已取消关机')
                    return False
                self.token, self.remaining = '', 0
                self.revision += 1
                self.state, self.message = 'committing', '已向 Windows 提交关机，无法保证撤回'
                logger.info('正在通过 Windows 系统 API 提交关机请求')
                shutdown_windows(logger.error)
                logger.info('%s', self.message)
                return False

    def _monitor(self, revision):
        try:
            while self._tick(revision, time.monotonic()):
                time.sleep(0.5)
        except Exception as exc:
            logger.exception('后台监控失败：完整异常堆栈')
            with self.lock:
                if self.revision == revision or self.state == 'committing':
                    self.revision += 1
                    self.token = ''
                    self.state, self.message = 'error', '失败：' + str(exc)
                    logger.info('%s', self.message)

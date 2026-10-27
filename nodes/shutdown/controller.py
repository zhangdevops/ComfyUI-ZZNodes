import logging
from collections import deque
from datetime import datetime
import math
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from . import diagnostics as diag
from .diagnostics import logger, register_secret, command_output, redact


class Controller:
    def __init__(self, queue_getter):
        self.queue_getter = queue_getter
        self.lock = threading.RLock()
        self.revision = 0
        self.token = ''
        self.state = 'disabled'
        self.message = '未启用；请主动打开 enabled'
        self.idle_seconds = 60
        self.countdown_seconds = 30
        self.remaining = 0
        self.idle_since = None
        self.history_start = set()
        self.stop_file = Path(__file__).resolve().parents[2] / 'STOP_SHUTDOWN.txt'
        self.logs = deque(maxlen=100)
        self._last_queue = None
        self._queue_log_time = 0
        self._log('自动关机控制已加载，尚未启用')
        self._log('诊断文件记录默认关闭，不写入磁盘')

    def _log(self, message):
        with self.lock:
            message = redact(message)
            self.logs.append(f'[{datetime.now().astimezone():%Y-%m-%d %H:%M:%S}] {message}')
            logger.info('流程事件：%s；state=%s；revision=%s', message, self.state, self.revision)

    def clear_logs(self):
        with self.lock:
            self.logs.clear()
            logger.info('已清空界面日志；不改变诊断记录开关及关机计划')

    def set_record_logs(self, enabled):
        with self.lock:
            path = diag.set_recording(enabled)
            self._log(('已开启诊断文件记录：' + str(path)) if enabled else '已关闭诊断文件记录，后续不再写入磁盘')

    def snapshot(self):
        with self.lock:
            return dict(revision=self.revision, arm_token=self.token, state=self.state,
                        message=self.message, remaining=self.remaining, enabled=bool(self.token),
                        idle_seconds=self.idle_seconds, countdown_seconds=self.countdown_seconds,
                        logs=list(self.logs), record_logs=diag.is_recording())

    def arm(self, revision, idle_seconds, countdown_seconds):
        with self.lock:
            logger.info('收到启用请求：请求revision=%s，当前revision=%s，idle_seconds=%s，countdown_seconds=%s', revision, self.revision, idle_seconds, countdown_seconds)
            if revision != self.revision:
                raise ValueError('状态已经变化，请刷新后重新开启')
            if type(idle_seconds) is not int or not 10 <= idle_seconds <= 86400:
                raise ValueError('空闲等待必须在 10～86400 秒之间')
            if type(countdown_seconds) is not int or not 10 <= countdown_seconds <= 600:
                raise ValueError('倒计时必须在 10～600 秒之间')
            if self.stop_file.exists():
                raise ValueError('请先删除 STOP_SHUTDOWN.txt')
            if os.name != 'nt':
                raise ValueError('真实关机只支持 Windows')
            if self.state == 'committing':
                raise ValueError('关机已提交，无法重新启用')
            self.history_start = set(self.queue_getter().get_history())
            self.revision += 1
            self.token = secrets.token_urlsafe(32)
            register_secret(self.token)
            self.idle_seconds, self.countdown_seconds = idle_seconds, countdown_seconds
            self.state, self.message = 'armed', '已准备；请提交包含本节点的工作流'
            self.remaining, self.idle_since = 0, None
            self._log(f'已启用自动关机；空闲等待 {idle_seconds} 秒；倒计时 {countdown_seconds} 秒')

    def cancel(self, reason):
        with self.lock:
            logger.info('收到取消请求；当前state=%s', self.state)
            if self.state == 'committing':
                self.message = 'Windows 关机已提交；无法保证撤回'
                self._log(self.message)
                return
            self.revision += 1
            self.token = ''
            self.state, self.message, self.remaining = 'cancelled', reason, 0
            self._log(reason)

    def submitted(self):
        with self.lock:
            logger.info('收到工作流提交事件；state=%s；已启用=%s', self.state, bool(self.token))
            if self.token and self.state in ('idle', 'countdown'):
                self._log('收到新任务提交，重新等待队列结束')
                self.state, self.message, self.remaining = 'waiting', '收到新任务，等待队列结束', 0
            self.idle_since = None

    def activate(self, token):
        register_secret(token)
        with self.lock:
            valid = bool(token) and isinstance(token, str) and isinstance(self.token, str) and secrets.compare_digest(token, self.token)
            logger.info('凭证校验：是否提供=%s，是否有效=%s，state=%s，revision=%s', bool(token), valid, self.state, self.revision)
            if not valid or self.state != 'armed':
                logger.warning('未启动监控：%s', '凭证缺失或无效' if not valid else '当前不是待启动状态（可能已启动或已取消）')
                return
            self.state, self.message = 'waiting', '等待全部排队及运行任务结束'
            self._log('工作流已执行，开始监控全部排队及运行任务')
            revision = self.revision
            threading.Thread(target=self._monitor, args=(revision,), daemon=True, name='QueueShutdown-1.0').start()

    def record_queue(self, queue, now):
        try:
            total = queue.get_tasks_remaining()
            getter = getattr(queue, 'get_current_queue_volatile', None) or getattr(queue, 'get_current_queue', None)
            running, pending = (None, None) if getter is None else getter()
            counts = (total, None if running is None else len(running), None if pending is None else len(pending))
            key = (counts, self.state)
            if key != self._last_queue or now - self._queue_log_time >= 30:
                logger.info('队列状态：总任务=%s，运行=%s，排队=%s，state=%s，剩余秒数=%s', *counts, self.state, self.remaining)
                self._last_queue, self._queue_log_time = key, now
        except Exception:
            # Diagnostic inspection must not change queue/shutdown behavior.
            logger.exception('读取附加队列诊断失败')

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
                logger.info('监控退出：版本已改变或凭证已失效；监控revision=%s，当前revision=%s', revision, self.revision)
                return False
            if self.stop_file.exists():
                self.cancel('已由 STOP_SHUTDOWN.txt 取消')
                return False
            queue = self.queue_getter()
            self.record_queue(queue, now)
            if self.failed_job(queue):
                self.cancel('检测到任务失败或中断，已取消关机')
                return False
            if queue.get_tasks_remaining():
                if self.state != 'waiting':
                    self._log('检测到运行或排队任务，重新等待队列结束')
                self.idle_since = None
                self.state, self.message, self.remaining = 'waiting', '等待全部排队及运行任务结束', 0
                return True
            if self.idle_since is None:
                self.idle_since = now
                self._log(f'队列已清空，开始空闲确认：{self.idle_seconds} 秒')
            elapsed = now - self.idle_since
            if elapsed < self.idle_seconds:
                self.state, self.remaining = 'idle', math.ceil(self.idle_seconds - elapsed)
                self.message = f'队列已空，空闲确认剩余 {self.remaining} 秒'
                return True
            total = self.idle_seconds + self.countdown_seconds
            if elapsed < total:
                if self.state != 'countdown':
                    self._log(f'开始关机倒计时：{self.countdown_seconds} 秒')
                self.state, self.remaining = 'countdown', math.ceil(total - elapsed)
                self.message = f'关机倒计时 {self.remaining} 秒；关闭 enabled 可取消'
                return True
            with queue.mutex:
                if queue.get_tasks_remaining():
                    self.idle_since = None
                    return True
                if self.failed_job(queue):
                    self.cancel('检测到任务失败或中断，已取消关机')
                    return False
                self.token, self.remaining = '', 0
                self.revision += 1
                self.state, self.message = 'committing', '已向 Windows 提交关机，无法保证撤回'
                self._log('正在向 Windows 提交关机命令')
                logger.info('执行固定命令：shutdown.exe /s /t 0；timeout=10秒')
                try:
                    result = subprocess.run(['shutdown.exe', '/s', '/t', '0'], check=False, timeout=10, capture_output=True)
                    logger.info('关机命令结束：returncode=%s\nstdout=%s\nstderr=%s', result.returncode, command_output(result.stdout), command_output(result.stderr))
                    if result.returncode:
                        raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
                except subprocess.TimeoutExpired as exc:
                    logger.error('关机命令超时：returncode=不可用\nstdout=%s\nstderr=%s', command_output(exc.output), command_output(exc.stderr))
                    raise
                except OSError:
                    logger.exception('关机命令无法启动：returncode=不可用')
                    raise
                self._log(self.message)
                logging.warning('[QueueShutdown] %s', self.message)
                return False

    def _monitor(self, revision):
        logger.info('后台监控线程启动；revision=%s', revision)
        try:
            while self._tick(revision, time.monotonic()):
                time.sleep(0.5)
        except Exception as exc:
            logger.exception('后台监控失败：完整异常堆栈')
            logging.error('[QueueShutdown] 监控失败：%s', ('请查看诊断日志：' + str(diag.LOG_PATH)) if diag.is_recording() else '诊断文件记录未开启，请查看节点运行日志')
            with self.lock:
                if self.revision == revision or self.state == 'committing':
                    self.revision += 1
                    self.token = ''
                    self.state, self.message = 'error', '失败：' + redact(exc)
                    self._log(self.message)
        finally:
            logger.info('后台监控线程结束；state=%s', self.state)

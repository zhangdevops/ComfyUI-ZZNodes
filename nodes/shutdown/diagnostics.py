"""独立诊断日志：轮转保存，格式化完整异常后统一屏蔽凭证。"""
import hashlib
import locale
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
import re
import sys
import tempfile
import threading

_secrets = set()
_secret_lock = threading.Lock()


def register_secret(value):
    if isinstance(value, str) and value:
        with _secret_lock:
            _secrets.add(value)


def redact(value):
    text = str(value)
    with _secret_lock:
        for secret in sorted(_secrets, key=len, reverse=True):
            text = text.replace(secret, '[REDACTED]')
    return re.sub(r'''(?i)(["']?arm_token["']?\s*[:=]\s*)(["'][^"']*["']|[^\s,;}]+)''',
                  r'\1[REDACTED]', text)


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        # Includes the exception's complete stack, but never traceback local variables.
        return redact(super().format(record))


logger = logging.getLogger('QueueShutdown.Diagnostics.' + hashlib.sha256(str(Path(__file__).resolve()).encode()).hexdigest()[:12])
logger.setLevel(logging.INFO)
logger.propagate = False
LOG_PATH = None

class SwitchableFileSink(logging.Handler):
    """同一把锁保护写入和关闭，关闭确认后不再追加文件。"""
    def __init__(self):
        super().__init__()
        self.writer = None

    def emit(self, record):
        # logging.Handler.handle() 已持有self.lock；关闭操作也使用同一把锁。
        if self.writer is not None:
            self.writer.handle(record)


_sink = SwitchableFileSink()
logger.addHandler(_sink)


def is_recording():
    with _sink.lock:
        return _sink.writer is not None


def set_recording(enabled):
    global LOG_PATH
    if type(enabled) is not bool:
        raise ValueError('记录诊断日志必须为布尔值')
    with _sink.lock:
        if enabled == (_sink.writer is not None):
            return LOG_PATH
        if not enabled:
            logger.info('已关闭诊断文件记录；停止后续磁盘写入')
            writer, _sink.writer = _sink.writer, None
            writer.close()
            return LOG_PATH
        # 只有用户明确开启，才创建目录和FileHandler；导入模块时不触碰磁盘。
        preferred = Path(__file__).resolve().parents[2] / 'logs' / 'queue_shutdown_diagnostic.log'
        fallback = Path(tempfile.gettempdir()) / 'ComfyUI-QueueShutdown' / (logger.name.rsplit('.', 1)[-1] + '.log')
        failures = []
        for target in (preferred, fallback):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                writer = RotatingFileHandler(target, maxBytes=2 * 1024 * 1024, backupCount=4, encoding='utf-8')
                LOG_PATH = str(target)
                break
            except OSError as exc:
                failures.append(f'{target}: {exc}')
        else:
            raise OSError('无法开启诊断文件记录：' + '; '.join(failures))
        writer.setFormatter(RedactingFormatter('%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
        _sink.writer = writer
        if failures:
            logger.warning('日志路径回退：%s', '; '.join(failures))
        logger.info('已开启诊断文件记录，版本=1.0；Python=%s；系统=%s；本地编码=%s；文件=%s',
                    sys.version.replace('\n', ' '), platform.platform(), locale.getpreferredencoding(False), LOG_PATH)
        return LOG_PATH


def command_output(value):
    if value is None:
        return ''
    if not isinstance(value, bytes):
        return redact(value)
    # Windows shutdown.exe 通常输出系统本地编码；同时保留原始字节，便于查乱码。
    for encoding in ('utf-8', locale.getpreferredencoding(False), 'gb18030'):
        try:
            return redact(value.decode(encoding))
        except (UnicodeError, LookupError):
            pass
    return redact(value.decode('utf-8', errors='replace'))

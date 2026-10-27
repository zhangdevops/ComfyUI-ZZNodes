"""自动关机控制1.0：独立诊断日志版。"""
import ipaddress
import secrets
from urllib.parse import urlsplit
from aiohttp import web
from server import PromptServer
from .controller import Controller
from .diagnostics import logger, register_secret

__version__ = '1.0'
controller = Controller(lambda: PromptServer.instance.prompt_queue)
_csrf = secrets.token_urlsafe(32)
register_secret(_csrf)

@web.middleware
async def reject_after_commit(request, handler):
    if request.method == 'POST' and request.path.rstrip('/') in ('/prompt', '/api/prompt'):
        if controller.snapshot()['state'] == 'committing':
            logger.warning('拒绝新的工作流请求：Windows关机已经提交')
            return web.json_response({'error': 'Windows shutdown already committed'}, status=503)
    return await handler(request)

PromptServer.instance.app.middlewares.append(reject_after_commit)

def local_request(request, mutation=False):
    try:
        local = ipaddress.ip_address(request.remote).is_loopback
    except (ValueError, TypeError):
        local = False
    host = urlsplit('http://' + request.host).hostname
    if not local or host not in ('localhost', '127.0.0.1', '::1'):
        raise web.HTTPForbidden(text='Local-only: open ComfyUI via localhost')
    origin = request.headers.get('Origin')
    if origin and origin != f'{request.scheme}://{request.host}':
        raise web.HTTPForbidden(text='Cross-origin request rejected')
    if mutation and not secrets.compare_digest(request.headers.get('X-Queue-Shutdown', ''), _csrf):
        raise web.HTTPForbidden(text='Invalid local session token')

@PromptServer.instance.routes.get('/queue_shutdown_v2/status')
async def status(request):
    local_request(request)
    return web.json_response({**controller.snapshot(), 'csrf': _csrf, 'version': __version__}, headers={'Cache-Control': 'no-store'})

@PromptServer.instance.routes.post('/queue_shutdown_v2/control')
async def control(request):
    try:
        local_request(request, True)
        data = await request.json()
        # Never dump request bodies, headers or complete status/prompt objects.
        if data.get('action') == 'cancel':
            controller.cancel('已由界面取消')
        elif data.get('action') == 'set_logging':
            controller.set_record_logs(data['record_logs'])
        elif data.get('action') == 'clear_logs':
            controller.clear_logs()
        elif data.get('action') == 'arm':
            if data.get('test_only') is True:
                raise web.HTTPBadRequest(text='Test mode was removed; this request was rejected without arming shutdown.')
            try:
                controller.arm(data['revision'], data['idle_seconds'], data['countdown_seconds'])
            except (ValueError, KeyError) as exc:
                logger.exception('启用请求失败：完整异常堆栈')
                raise web.HTTPConflict(text=str(exc))
        else:
            raise web.HTTPBadRequest(text='Unknown action')
        return web.json_response(controller.snapshot())
    except web.HTTPException as exc:
        logger.warning('控制接口未成功：HTTP=%s；原因=%s', exc.status, exc.text)
        raise
    except Exception:
        logger.exception('控制接口异常：完整异常堆栈')
        raise

def on_prompt(data):
    controller.submitted()
    return data

PromptServer.instance.add_on_prompt_handler(on_prompt)

class QueueShutdown:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'enabled': ('BOOLEAN', {'default': False}),
            'idle_seconds': ('INT', {'default': 60, 'min': 10, 'max': 86400}),
            'countdown_seconds': ('INT', {'default': 30, 'min': 10, 'max': 600}),
            'record_logs': ('BOOLEAN', {'default': False}),
            'arm_token': ('STRING', {'default': ''}),
        }}
    RETURN_TYPES = ()
    FUNCTION = 'execute'
    OUTPUT_NODE = True
    CATEGORY = 'System'

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float('nan')

    def execute(self, enabled=False, idle_seconds=60, countdown_seconds=30, record_logs=False, arm_token=''):
        # 记录开关由界面实时操作；旧排队任务中的record_logs值不会开启磁盘记录。
        register_secret(arm_token)
        logger.info('节点execute进入：enabled=%s，凭证是否提供=%s，idle_seconds=%s，countdown_seconds=%s', bool(enabled), bool(arm_token), idle_seconds, countdown_seconds)
        try:
            if enabled:
                controller.activate(arm_token)
            else:
                logger.info('本次节点enabled=false，不启动监控；不改变全局计划')
            return ()
        except Exception:
            logger.exception('节点execute失败：完整异常堆栈')
            raise

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

// Evaluate the actual extension with stubbed ComfyUI imports, without a browser.
const source = fs.readFileSync(path.join(__dirname, '../web/shutdown/shutdown.js'), 'utf8');
const sandbox = { app: { registerExtension() {} } };
vm.createContext(sandbox);
vm.runInContext(source.replace(/^import .*;\r?\n/gm, ''), sandbox);

for (const fail of [false, true]) {
    test(`badge uses real canvas and restores methods (throw=${fail})`, () => {
        const calls = [];
        const prototype = {};
        const ctx = Object.create(prototype);
        for (const key of ['measureText', 'fillText', 'strokeText']) {
            prototype[key] = function (...args) {
                assert.equal(this, ctx);
                calls.push([key, ...args]);
                return 42;
            };
        }
        // One own property and two inherited methods must retain their descriptors.
        Object.defineProperty(ctx, 'strokeText', {
            value: prototype.strokeText, configurable: true, writable: false, enumerable: false,
        });
        const before = Object.getOwnPropertyDescriptors(ctx);
        const node = { drawBadges(canvas, arg) {
            assert.equal(this, node);
            assert.equal(canvas, ctx);
            assert.equal(arg, 'arg');
            assert.equal(canvas.measureText('ZZNodes'), 42);
            canvas.fillText('#12 ComfyUI-ZZNodes', 1, 2, 100);
            canvas.strokeText('unrelated', 3, 4);
            if (fail) throw new Error('draw failure');
            return 'result';
        } };
        sandbox.localizeSourceBadge(node);
        const wrapped = node.drawBadges;
        sandbox.localizeSourceBadge(node);
        assert.equal(node.drawBadges, wrapped);
        if (fail) assert.throws(() => node.drawBadges(ctx, 'arg'), /draw failure/);
        else assert.equal(node.drawBadges(ctx, 'arg'), 'result');
        assert.deepEqual(calls, [
            ['measureText', '自动关机控制'],
            ['fillText', '#12 自动关机控制', 1, 2, 100],
            ['strokeText', 'unrelated', 3, 4],
        ]);
        assert.deepEqual(Object.getOwnPropertyDescriptors(ctx), before);
    });
}

test('minimal UI synchronizes logs, arms/cancels and discards old workflow consent', async () => {
    const requests = [];
    let extension;
    let state = { revision: 0, csrf: 'test-session', enabled: false, arm_token: '',
        message: '未启用', logs: ['已加载'] };
    const context = {
        AbortSignal,
        window: { alert(message) { assert.fail(message); } },
        app: { registerExtension(value) { extension = value; } },
        api: { async fetchApi(url, options) {
            if (options.method === 'POST') {
                const body = JSON.parse(options.body);
                requests.push(body);
                assert.deepEqual(Object.keys(body).sort(), ['action', 'revision']);
                assert.equal(options.headers['X-Queue-Shutdown'], 'test-session');
                state = { ...state, revision: state.revision + 1,
                    enabled: body.action === 'arm',
                    arm_token: body.action === 'arm' ? 'new-consent' : '',
                    message: body.action === 'arm' ? '队列空闲确认剩余 30 秒' : '已由界面取消' };
            }
            return { ok: true, async json() { return state; } };
        } },
        ComfyWidgets: { STRING(node, name) {
            const widget = { name, value: '', inputEl: { style: {}, scrollHeight: 1,
                scrollTop: 0, clientHeight: 1 } };
            node.widgets.push(widget);
            return { widget };
        } },
    };
    vm.createContext(context);
    vm.runInContext(source.replace(/^import .*;\r?\n/gm, ''), context);
    const enabled = { name: 'enabled', value: false };
    const token = { name: 'arm_token', value: '' };
    const node = { comfyClass: 'QueueShutdown', widgets: [enabled, token],
        setDirtyCanvas() {}, setSize(size) { this.size = size; },
        addWidget() { assert.fail('No extra buttons should be added'); } };
    extension.nodeCreated(node);
    await new Promise(setImmediate);
    assert.deepEqual(node.widgets.filter(w => !w.hidden).map(w => w.name), ['enabled', '运行日志']);
    assert.equal(enabled.label, '启用自动关机');
    assert.equal(node._shutdownLog.inputEl.readOnly, true);
    enabled.callback(true);
    await new Promise(setImmediate);
    assert.equal(enabled.value, true);
    assert.equal(token.value, 'new-consent');
    assert.match(node._shutdownLog.value, /空闲确认剩余 30 秒/);
    const serialized = { widgets_values: [true, 'new-consent'] };
    node.onSerialize(serialized);
    assert.equal(serialized.widgets_values[1], '');
    enabled.callback(false);
    await new Promise(setImmediate);
    assert.equal(enabled.value, false);
    assert.equal(token.value, '');
    // Simulate positional values from the previous five-input workflow schema.
    enabled.value = true;
    token.value = 60;
    node._shutdownLog.value = 30;
    node.onConfigure({ widgets_values: [true, 60, 30, true, 'old-token'] });
    assert.equal(enabled.value, false);
    assert.equal(token.value, '');
    await new Promise(setImmediate);
    assert.match(node._shutdownLog.value, /已由界面取消/);
    assert.deepEqual(requests.map(x => x.action), ['arm', 'cancel']);
});

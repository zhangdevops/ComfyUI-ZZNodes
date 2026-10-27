import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { ComfyWidgets } from "../../../scripts/widgets.js";

const nodes = new Set();
let backend = null;
let pending = 0;
let chain = Promise.resolve();
let requestVersion = 0;
const widget = (n, name) => n.widgets?.find(w => w.name === name);
const fetchLocal = (path, options = {}) => api.fetchApi(path, { ...options, signal: AbortSignal.timeout(5000) });

function hideArmToken(node) {
    const token = widget(node, "arm_token");
    if (!token) return;
    token.type = "hidden";
    token.hidden = true;
    token.options ??= {};
    token.options.hidden = true;
    token.draw = () => {};
    token.computeSize = () => [0, -4];
    for (const element of new Set([token.inputEl, token.element])) {
        if (!element?.style) continue;
        element.hidden = true;
        element.style.setProperty("display", "none", "important");
        element.style.setProperty("visibility", "hidden", "important");
        element.style.setProperty("pointer-events", "none", "important");
        element.setAttribute?.("aria-hidden", "true");
        element.setAttribute?.("tabindex", "-1");
    }
}

function localizeSourceBadge(node) {
    if (node._queueShutdownBadgeLocalized || typeof node.drawBadges !== "function") return;
    const original = node.drawBadges;
    const label = text => {
        if (typeof text !== "string") return text;
        if (text === "QueueShutdown" || text === "ComfyUI-QueueShutdown" || text === "ZZNodes" || text === "ComfyUI-ZZNodes") return "自动关机控制";
        return text.replace(/^(#\d+\s+)(?:ComfyUI-)?(?:QueueShutdown|ZZNodes)$/, "$1自动关机控制");
    };
    node.drawBadges = function (ctx, ...args) {
        const translated = new Proxy(ctx, {
            get(target, key) {
                if (key === "measureText") return text => target.measureText(label(text));
                if (key === "fillText" || key === "strokeText") return (text, ...rest) => target[key](label(text), ...rest);
                const value = Reflect.get(target, key, target);
                return typeof value === "function" ? value.bind(target) : value;
            },
            set(target, key, value) { return Reflect.set(target, key, value, target); },
        });
        return original.call(this, translated, ...args);
    };
    Object.defineProperty(node, "_queueShutdownBadgeLocalized", { value: true });
}

function display(message, sync = false) {
    for (const node of nodes) {
        hideArmToken(node);
        if (!sync && node._shutdownLog) {
            const logs = (backend?.logs ?? []).join("\n");
            node._shutdownLog.value = `${logs}${logs ? "\n" : ""}【当前提示】${message}`;
        }
        if (sync && backend) {
            const text = (backend.logs ?? []).join("\n");
            const w = node._shutdownLog;
            if (w && w.value !== text) {
                const input = w.inputEl;
                const bottom = !input || input.scrollHeight - input.scrollTop - input.clientHeight < 24;
                w.value = text;
                if (input && bottom) input.scrollTop = input.scrollHeight;
            }
            widget(node, "record_logs").value = Boolean(backend.record_logs);
            widget(node, "enabled").value = backend.enabled;
            widget(node, "arm_token").value = backend.arm_token;
            for (const key of ["idle_seconds", "countdown_seconds"]) {
                const w = widget(node, key);
                if (backend.enabled) w.value = backend[key];
                w.disabled = backend.enabled;
            }
        }
        node.setDirtyCanvas(true, true);
    }
}

async function refresh() {
    if (pending) return;
    const version = requestVersion;
    try {
        const response = await fetchLocal("/queue_shutdown_v2/status", { cache: "no-store" });
        if (!response.ok) throw new Error(await response.text());
        const state = await response.json();
        if (pending || version !== requestVersion) return;
        backend = state;
        display(state.message, true);
    } catch (error) {
        if (!pending) display("状态未知：无法连接后台；不代表关机已取消");
    }
}

function control(action, node) {
    const revision = backend?.revision;
    const settings = { idle_seconds: widget(node, "idle_seconds").value, countdown_seconds: widget(node, "countdown_seconds").value, record_logs: widget(node, "record_logs").value };
    pending++;
    const version = ++requestVersion;
    display(action === "cancel" ? "正在请求取消，请等待后台确认…" : action === "clear_logs" ? "正在清空日志…" : action === "set_logging" ? "正在设置诊断文件记录…" : "正在请求启用…");
    const operation = (async () => {
        if (action === "arm" && revision == null) throw new Error("尚未读取后台状态，请稍后重试");
        let current = backend;
        if (!current?.csrf) {
            const response = await fetchLocal("/queue_shutdown_v2/status", { cache: "no-store" });
            if (!response.ok) throw new Error(await response.text());
            current = await response.json();
        }
        const response = await fetchLocal("/queue_shutdown_v2/control", {
            method: "POST", headers: { "Content-Type": "application/json", "X-Queue-Shutdown": current.csrf },
            body: JSON.stringify({ action, revision, ...settings }),
        });
        if (!response.ok) throw new Error(await response.text());
        const confirmed = { ...await response.json(), csrf: current.csrf };
        if (version !== requestVersion) return;
        backend = confirmed;
        display(backend.message, true);
    })().catch(error => {
        if (version !== requestVersion) return;
        if (action === "set_logging") {
            display(`诊断日志设置未确认：${error.message}；关机计划不变`);
            window.alert(`诊断日志设置失败：${error.message}`);
            return;
        }
        if (action === "clear_logs") {
            display(`清空日志未确认：${error.message}；关机监控未被取消`);
            window.alert(`清空日志失败：${error.message}`);
            return;
        }
        display(`操作未确认：${error.message}；可再次关闭“启用自动关机”重试`);
        window.alert(`队列关机：操作未确认。${error.message}\n取消失败时请再次关闭“启用自动关机”重试，或创建 STOP_SHUTDOWN.txt。`);
    }).finally(() => { pending--; });
    chain = Promise.allSettled([chain, operation]);
}

app.registerExtension({
    name: "QueueShutdown.V1.0",
    nodeCreated(node) {
        if (node.comfyClass !== "QueueShutdown") return;
        localizeSourceBadge(node);
        node.title = "自动关机控制";
        const labels = { enabled: "启用自动关机", idle_seconds: "队列空闲等待（秒）", countdown_seconds: "关机倒计时（秒）", record_logs: "记录诊断日志" };
        for (const w of node.widgets ?? []) {
            if (labels[w.name]) w.label = labels[w.name];
            if (w.name === "enabled" || w.name === "record_logs") { w.options ??= {}; w.options.on = "开启"; w.options.off = "关闭"; }
        }
        nodes.add(node);
        const token = widget(node, "arm_token");
        hideArmToken(node);
        token.serializeValue = () => token.value;
        token.value = "";
        const oldConfigure = node.onConfigure;
        node.onConfigure = function (...args) {
            try { return oldConfigure?.apply(this, args); }
            finally { hideArmToken(this); }
        };
        const oldSerialize = node.onSerialize;
        node.onSerialize = function (data) {
            oldSerialize?.call(this, data);
            if (data.widgets_values) data.widgets_values[this.widgets.indexOf(token)] = "";
        };
        const enabled = widget(node, "enabled");
        enabled.value = false;
        const callback = enabled.callback;
        enabled.callback = function (value, ...args) {
            callback?.call(this, value, ...args);
            control(value ? "arm" : "cancel", node);
        };
        const recordLogs = widget(node, "record_logs");
        recordLogs.value = false;
        const logCallback = recordLogs.callback;
        recordLogs.callback = function (value, ...args) {
            logCallback?.call(this, value, ...args);
            control("set_logging", node);
        };
        const w = ComfyWidgets.STRING(node, "运行日志", ["STRING", { multiline: true, default: "" }], app).widget;
        w.options ??= {};
        w.options.serialize = false;
        w.computeSize = () => [560, 220];
        if (w.inputEl) {
            w.inputEl.readOnly = true;
            w.inputEl.style.fontFamily = "monospace";
            w.inputEl.style.fontSize = "13px";
            w.inputEl.style.overflowY = "auto";
            w.inputEl.placeholder = "运行日志（保留最近100条）";
        }
        node._shutdownLog = w;
        node.addWidget("button", "清空日志", null, () => control("clear_logs", node), { serialize: false });
        const removed = node.onRemoved;
        node.onRemoved = function (...args) { nodes.delete(this); return removed?.apply(this, args); };
        node.setSize([640, 520]);
        refresh();
    },
    setup() { setInterval(() => { if (nodes.size) refresh(); }, 1000); },
});

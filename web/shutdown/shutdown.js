import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { ComfyWidgets } from "../../../scripts/widgets.js";

const nodes = new Set();
let backend = null;
let pending = 0;
let requestVersion = 0;
const widget = (n, name) => n.widgets?.find(w => w.name === name);

async function fetchLocal(path, options = {}) {
    const response = await api.fetchApi(path, { ...options, signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
}

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
        // Use the real canvas context. Restore exact properties even if drawing fails.
        const saved = [];
        try {
            for (const key of ["measureText", "fillText", "strokeText"]) {
                const method = ctx[key];
                saved.push([key, Object.getOwnPropertyDescriptor(ctx, key)]);
                Object.defineProperty(ctx, key, {
                    configurable: true, writable: true,
                    value(text, ...rest) { return method.call(this, label(text), ...rest); },
                });
            }
            return original.call(this, ctx, ...args);
        } finally {
            for (const [key, descriptor] of saved.reverse()) {
                if (descriptor) Object.defineProperty(ctx, key, descriptor);
                else delete ctx[key];
            }
        }
    };
    Object.defineProperty(node, "_queueShutdownBadgeLocalized", { value: true });
}

function display(message, sync = false) {
    const history = (backend?.logs ?? []).join("\n");
    const text = `${history}${history ? "\n" : ""}【${sync ? "当前状态" : "当前提示"}】${message}`;
    for (const node of nodes) {
        hideArmToken(node);
        const w = node._shutdownLog;
        if (w && w.value !== text) {
            const input = w.inputEl;
            const bottom = !input || input.scrollHeight - input.scrollTop - input.clientHeight < 24;
            w.value = text;
            if (input && bottom) input.scrollTop = input.scrollHeight;
        }
        if (sync && backend) {
            widget(node, "enabled").value = backend.enabled;
            widget(node, "arm_token").value = backend.arm_token;
        }
        node.setDirtyCanvas(true, true);
    }
}

async function refresh() {
    if (pending) return;
    const version = requestVersion;
    try {
        const state = await fetchLocal("/queue_shutdown_v2/status", { cache: "no-store" });
        if (pending || version !== requestVersion) return;
        backend = state;
        display(state.message, true);
    } catch {
        if (!pending) display("状态未知：无法连接后台；不代表关机已取消");
    }
}

async function control(action) {
    const revision = backend?.revision;
    pending++;
    const version = ++requestVersion;
    display(action === "cancel" ? "正在请求取消，请等待后台确认…" : "正在请求启用…");
    try {
        if (action === "arm" && revision == null) throw new Error("尚未读取后台状态，请稍后重试");
        let current = backend;
        if (!current?.csrf) {
            current = await fetchLocal("/queue_shutdown_v2/status", { cache: "no-store" });
        }
        const confirmed = await fetchLocal("/queue_shutdown_v2/control", {
            method: "POST", headers: { "Content-Type": "application/json", "X-Queue-Shutdown": current.csrf },
            body: JSON.stringify({ action, revision }),
        });
        if (version !== requestVersion) return;
        backend = { ...confirmed, csrf: current.csrf };
        display(backend.message, true);
    } catch (error) {
        if (version !== requestVersion) return;
        display(`操作未确认：${error.message}；可再次关闭“启用自动关机”重试`);
        window.alert(`队列关机：操作未确认。${error.message}\n取消失败时请再次关闭“启用自动关机”重试，或创建 STOP_SHUTDOWN.txt。`);
    } finally {
        pending--;
    }
}

app.registerExtension({
    name: "QueueShutdown.V1.0",
    nodeCreated(node) {
        if (node.comfyClass !== "QueueShutdown") return;
        localizeSourceBadge(node);
        node.title = "自动关机控制";
        nodes.add(node);
        const token = widget(node, "arm_token");
        hideArmToken(node);
        token.serializeValue = () => token.value;
        token.value = "";
        const oldConfigure = node.onConfigure;
        node.onConfigure = function (...args) {
            try { return oldConfigure?.apply(this, args); }
            finally {
                // Older workflows store removed settings by position. Never use
                // their saved values as a current session's shutdown consent.
                token.value = "";
                widget(this, "enabled").value = false;
                if (this._shutdownLog) this._shutdownLog.value = "正在读取后台状态…";
                hideArmToken(this);
                refresh();
            }
        };
        const oldSerialize = node.onSerialize;
        node.onSerialize = function (data) {
            oldSerialize?.call(this, data);
            if (data.widgets_values) data.widgets_values[this.widgets.indexOf(token)] = "";
        };
        const enabled = widget(node, "enabled");
        enabled.label = "启用自动关机";
        enabled.options ??= {};
        enabled.options.on = "开启";
        enabled.options.off = "关闭";
        enabled.value = false;
        const callback = enabled.callback;
        enabled.callback = function (value, ...args) {
            callback?.call(this, value, ...args);
            control(value ? "arm" : "cancel");
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
        const removed = node.onRemoved;
        node.onRemoved = function (...args) { nodes.delete(this); return removed?.apply(this, args); };
        node.setSize([640, 340]);
        refresh();
    },
    setup() { setInterval(() => { if (nodes.size) refresh(); }, 1000); },
});

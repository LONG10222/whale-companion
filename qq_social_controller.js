/* No model tools, persistent browser storage, automatic authentication or retries. */
(function (root) {
  'use strict';
  function createController(api, clock = () => Date.now() / 1000, changed = () => {}) {
    const state = {connected:false, enabled:false, ready:false, busy:false, preview:null,
      phase:'disconnected', notice:'连接本机后，先核对机器人账号。', account:'', uncertain:false, lastAttempt:null};
    let revision = 0;
    const emit = () => changed(state);
    function invalidate(message) {
      revision++; state.preview = null;
      if (message && !state.uncertain && state.phase !== 'sending') state.notice = message;
      emit();
    }
    async function connect() {
      if (state.busy) return;
      invalidate(); state.busy = true; state.connected = false; state.ready = false; state.enabled = false; emit();
      try {
        await api('/local/session', {}, true);
        const status = await api('/qq/social/status');
        const qq = await api('/qq/status');
        state.connected = true; state.enabled = status.enabled === true;
        state.account = String(qq.login_id || '');
        state.ready = Boolean(qq.connected && qq.account_matches && qq.enabled);
        state.phase = state.uncertain ? 'unknown' : 'ready';
        state.notice = state.uncertain ? '上次结果仍不确定。先到 QQ 人工核对，不能自动重试。' :
          '已连接本机。社交默认关闭；接口适配不代表当前账号已验证。';
      } catch (_) {
        state.phase = 'disconnected'; state.notice = '本机连接失败或会话失效，请检查程序后手动重新连接。';
      } finally { state.busy = false; emit(); }
    }
    async function configure(enabled) {
      if (state.busy || !state.connected) return;
      invalidate(); state.busy = true; emit();
      try {
        const result = await api('/qq/social/config', {enabled:enabled === true});
        state.enabled = result.enabled === true;
        state.notice = state.enabled ? '已手动启用。每项操作仍需单独预览和确认；重启后会关闭。' : '社交已关闭，待确认预览已清除。';
      } catch (e) {
        state.connected = false; state.ready = false; state.enabled = false;
        state.notice = '开关状态未确认。请手动重新连接核对，未自动重试。';
      } finally { state.busy = false; emit(); }
    }
    async function requests() {
      if (state.busy || !state.connected || !state.enabled || !state.ready) return [];
      invalidate('刷新会清除待确认预览。'); state.busy = true; emit();
      try { const result = await api('/qq/social/requests'); return result.requests || []; }
      catch (e) { state.connected = false; state.enabled = false; state.ready = false; state.notice = '无法读取申请。请核对 QQ 连接；只显示本次连接实际收到的申请。'; return []; }
      finally { state.busy = false; emit(); }
    }
    async function prepare(operation) {
      if (state.busy || !state.connected || !state.enabled || !state.ready || state.uncertain) return;
      invalidate(); state.lastAttempt = null; const version = revision; state.busy = true; emit();
      try {
        const result = await api('/qq/social/prepare', {operation:JSON.parse(JSON.stringify(operation))});
        if (version !== revision) { state.notice = '输入已改变，旧预览已丢弃，请重新预览。'; return; }
        if (!result || !result.preview || typeof result.nonce !== 'string' || !Number.isFinite(result.expires) || result.expires <= clock())
          throw Error('预览已失效');
        state.preview = JSON.parse(JSON.stringify(result));
        state.phase = 'preview'; state.notice = '尚未执行。请逐项核对下方完整预览。';
      } catch (e) {
        state.preview = null; state.phase = 'ready';
        if (e.status === 401) { state.connected = false; state.ready = false; state.enabled = false; }
        state.notice = typeof e.message === 'string' ? e.message.slice(0,240) : '无法准备操作，请核对连接和权限。';
      } finally { state.busy = false; emit(); }
    }
    function tick() {
      if (state.preview && state.preview.expires <= clock()) {
        invalidate('确认已过期，尚未执行。请重新生成预览。'); state.phase = 'ready';
      }
      emit();
    }
    async function confirm(checked) {
      tick();
      if (checked !== true || state.busy || !state.preview || !state.connected || !state.enabled || state.uncertain) return;
      const item = state.preview; state.lastAttempt = JSON.parse(JSON.stringify(item.preview));
      state.preview = null; revision++; state.busy = true; state.phase = 'sending';
      state.notice = '正在执行一次。请勿关闭页面；不会自动重发。'; emit();
      try {
        const result = await api('/qq/social/confirm', {nonce:item.nonce, preview:item.preview, confirmed:true});
        if (!result || result.state !== 'confirmed') throw Error('缺少成功回执');
        state.phase = 'confirmed'; state.notice = '连接器已返回成功回执。请到 QQ 核对最终结果；不会自动重发。';
        return result;
      } catch (_) {
        state.uncertain = true; state.phase = 'unknown';
        state.notice = '结果未确认，可能已经执行。必须先到 QQ 人工核对；本页面不会重试。';
      } finally { state.busy = false; emit(); }
    }
    function afterManualCheck() {
      if (state.busy) return;
      invalidate(); state.lastAttempt = null; state.uncertain = false; state.phase = 'ready';
      state.notice = '已结束上次操作。这里只能准备新操作，相同操作仍受后台防重保护。'; emit();
    }
    return {state, connect, configure, requests, prepare, confirm, invalidate, tick, afterManualCheck};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {createController};
  else root.WhaleSocial = {createController};
})(typeof window !== 'undefined' ? window : globalThis);

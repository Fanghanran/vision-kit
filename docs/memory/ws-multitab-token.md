---
name: ws-multitab-token
description: 多标签页token失效连锁问题 - BroadcastChannel方案已实现
metadata: 
  node_type: memory
  type: project
  originSessionId: a44998c4-778d-42f2-b4c3-58b9c24bcb0c
---

## 问题

P0 实现的 `useWebSocket.ts` 中，收到 4001（token失效）时执行 `localStorage.removeItem('va-token')`，导致所有标签页的 token 被清除，其他标签页也被踢到登录页。

## 解决方案：BroadcastChannel（已实现）

**实现文件**：
- `frontend/src/composables/useMultiTabSync.ts` ✅
- `frontend/src/composables/useWebSocket.ts` ✅
- `frontend/src/stores/auth.ts` ✅
- `frontend/src/App.vue` ✅

**实现逻辑**：
1. `useMultiTabSync.ts` 创建 `BroadcastChannel('vision-agent-auth')`
2. 标签页 token 过期时广播 `{ type: 'token_expired' }`
3. 其他标签页收到广播 → 3 秒后同步登出
4. 正常登出时广播 `{ type: 'logout' }` → 所有标签页立即同步
5. Token 刷新时广播 `{ type: 'token_refreshed', token }` → 其他标签页同步新 token

**Why:** 优化多标签用户体验，避免误杀有效标签页
**How to:** 已完成，无需后续操作

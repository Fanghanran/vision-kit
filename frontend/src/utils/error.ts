/**
 * 前端统一错误处理
 *
 * 用法：
 *   import { handleError, ErrorLevel } from '@/utils/error'
 *   try { ... } catch (e) { handleError(e, ErrorLevel.ERROR, '获取告警列表') }
 */

import { ElMessage } from 'element-plus'

export enum ErrorLevel {
  INFO = 'info',
  WARN = 'warning',
  ERROR = 'error',
}

interface ErrorDetail {
  status?: number
  message: string
  url?: string
  timestamp: string
}

let errorCallback: ((detail: ErrorDetail) => void) | null = null

/** 注册外部错误回调（如 Sentry） */
export function onError(cb: (detail: ErrorDetail) => void) {
  errorCallback = cb
}

/** 从 axios / fetch 异常中提取信息 */
export function extractError(e: any): ErrorDetail {
  const detail: ErrorDetail = {
    message: '未知错误',
    timestamp: new Date().toISOString(),
  }

  if (e?.response) {
    // axios 错误
    detail.status = e.response.status
    detail.message = e.response.data?.detail || e.message || `HTTP ${e.response.status}`
    detail.url = e.response.config?.url
  } else if (e?.message) {
    detail.message = e.message
  } else if (typeof e === 'string') {
    detail.message = e
  }

  return detail
}

/** 统一错误处理：弹窗 + 控制台 + 可选回调 */
export function handleError(e: any, level: ErrorLevel = ErrorLevel.ERROR, context = '') {
  const detail = extractError(e)

  // 控制台结构化日志
  const prefix = `[${context || 'app'}]`
  switch (level) {
    case ErrorLevel.INFO:
      console.log(prefix, detail.message, detail)
      break
    case ErrorLevel.WARN:
      console.warn(prefix, detail.message, detail)
      break
    case ErrorLevel.ERROR:
      console.error(prefix, detail.message, detail)
      break
  }

  // 用户可见提示
  if (level === ErrorLevel.ERROR) {
    ElMessage.error(context ? `${context}失败：${detail.message}` : detail.message)
  } else if (level === ErrorLevel.WARN) {
    ElMessage.warning(detail.message)
  }

  // 外部回调（Sentry 等）
  if (errorCallback) {
    errorCallback(detail)
  }
}

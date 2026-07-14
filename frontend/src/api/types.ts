// 后端 API 类型定义

export interface HealthResponse {
  status: 'ok' | 'degraded' | 'unhealthy'
  uptime_seconds: number
  gpu_utilization: number
  gpu_memory_used_mb: number
  gpu_memory_total_mb: number
  queue_depth: number
  inference_latency_p50_ms: number
  inference_latency_p99_ms: number
  active_cameras: number
  total_cameras: number
  today_alerts: number
  llm_success_rate: number
  warning?: string
}

export interface CameraState {
  camera_id: string
  status: string
  current_fps: number
  gpu_latency_ms: number
  queue_size: number
  last_frame_time: number
  total_detections: number
  total_alerts: number
  uptime_seconds: number
  error_message: string
}

export interface Alert {
  alert_id: string
  event_type: string
  camera_id: string
  camera_name: string
  severity: 'info' | 'warning' | 'critical'
  status: 'pending' | 'acknowledged' | 'rejected' | 'resolved'
  risk_level?: string
  created_at: number
  llm_analysis?: {
    description: string
    risk_level: string
    suggestion: string
    context: string
    raw_response?: string
  }
  snapshot_path?: string
  video_clip_path?: string
}

export interface AlertListResponse {
  items: Alert[]
  total: number
  page: number
  page_size: number
}

export interface AlertFilters {
  status?: string
  camera_id?: string
  event_type?: string
  severity?: string
  start_time?: number
  end_time?: number
}

export interface SystemStats {
  period: string
  total_alerts: number
  yesterday_total?: number
  alerts_by_type: Record<string, number>
  alerts_by_severity: Record<string, number>
  alerts_by_camera: Record<string, number>
  alerts_by_status: Record<string, number>
  active_cameras: number
  system_uptime_hours: number
}

export interface WSMessage {
  type: 'new_alert' | 'alert_status' | 'system_status' | 'camera_status' | 'ping' | 'pong'
  [key: string]: any
}

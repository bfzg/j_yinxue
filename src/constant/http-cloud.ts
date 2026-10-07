/**
 * 一个云函数对应一条 URL 化路径，功能用 body 里的 action 区分。
 *
 * 路径在 uniCloud 控制台「云函数 → 配置 URL 化」里手工绑定，
 * 名字统一带 jy- 前缀：这个服务空间与另一个项目共用，/upload 已被占用。
 */

/** function-jy-content → /jy-content（读公开，写需要管理令牌） */
export const CLOUD_HTTP_PATH_CONTENT = '/jy-content'

/**
 * 打包时间戳，由 vite define 注入。
 * 用 typeof 取值：万一 define 没生效（比如改了 vite.config 但没重启监听），
 * 也只是显示 dev，不会在真机上抛 ReferenceError。
 */
export const BUILD_STAMP = typeof __BUILD_STAMP__ === 'string' ? __BUILD_STAMP__ : 'dev'

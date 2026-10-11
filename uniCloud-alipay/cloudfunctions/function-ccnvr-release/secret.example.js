'use strict'

// 复制本文件为 secret.js 并填入随机长串，然后右键上传整个 function-ccnvr-release 目录。
// 更推荐的做法是在 uniCloud 控制台给云函数配置环境变量 ADMIN_TOKEN，这样不用改代码。
// secret.js 已在仓库 .gitignore 里（**/secret.js），不会被提交。
// 发布脚本 scripts/push-release.mjs 与后台页面要用同一个令牌。

module.exports = {
  ADMIN_TOKEN: '换成你自己的随机串，至少 32 位',
}

'use strict';

/**
 * 写操作鉴权：单因子 ADMIN_TOKEN
 *
 * token 来源二选一（前者优先）：
 *   1. 云函数环境变量 ADMIN_TOKEN（控制台配置，换 token 不必重传代码）
 *   2. 同目录 secret.js（本地填好再上传，别提交进仓库）
 * 调用方用请求头 x-admin-token，或 body/query 里的 token 传。
 */

const crypto = require('crypto');

function secretFromFile() {
  try {
    // 文件不存在时 require 会抛错，忽略即可，回落到环境变量
    return require('./secret.js').ADMIN_TOKEN || '';
  } catch (err) {
    return '';
  }
}

function expectedToken() {
  return String(process.env.ADMIN_TOKEN || secretFromFile() || '');
}

function givenToken(body, query, headers) {
  return String(
    (headers && (headers['x-admin-token'] || headers['X-Admin-Token']))
    || (body && body.token) || (query && query.token) || '',
  );
}

function safeEqual(a, b) {
  const bufA = Buffer.from(String(a));
  const bufB = Buffer.from(String(b));
  if (bufA.length === 0 || bufA.length !== bufB.length) {
    return false;
  }
  return crypto.timingSafeEqual(bufA, bufB);
}

/** 放行返回 null，否则返回一条错误响应体 */
function requireAdmin(body, query, headers) {
  const expected = expectedToken();
  if (!expected) {
    return {
      code: 500,
      message: '云函数未配置 ADMIN_TOKEN：在控制台加环境变量，或把 secret.example.js 复制成 secret.js 再上传',
    };
  }
  if (!safeEqual(expected, givenToken(body, query, headers))) {
    return { code: 401, message: '管理令牌不正确' };
  }
  return null;
}

module.exports = { requireAdmin, expectedToken };

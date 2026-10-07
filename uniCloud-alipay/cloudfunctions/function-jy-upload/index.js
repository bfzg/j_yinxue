'use strict';

/**
 * /jy-upload 入口：把音频和正文推到 uniCloud 云存储，全部动作都要管理令牌。
 *
 * 支付宝云 URL 化的请求体上限是 32MB，最大一集音频 12MB 左右，
 * base64 后约 17MB，仍在上限内，所以整文件单请求上传，不做分片。
 *
 * put 的三种内容来源，按优先级：
 *   1. body.content + body.encoding === 'base64'
 *   2. 整个 event.body 就是 base64（isBase64Encoded 为真，走原始字节，最省体积）
 *   3. body.content 当 utf8 文本存（正文 .txt 用得上）
 */

const crypto = require('crypto');
const admin = require('./admin.js');
const pkg = require('./package.json');

/** 只放行这两类前缀，防止这个函数变成任意文件写入器 */
const ALLOW_PREFIXES = ['jiugeyinxue/'];
const MAX_BYTES = 30 * 1024 * 1024;

function parseEvent(event) {
  const e = event || {};
  const headers = e.headers || {};
  const query = e.queryStringParameters || e.query || {};
  let body = {};
  let rawBuffer = null;

  if (typeof e.body === 'string' && e.body.length) {
    if (e.isBase64Encoded) {
      rawBuffer = Buffer.from(e.body, 'base64');
      // 原始字节直传时 body 不是 JSON，参数只能从 querystring 取
      try {
        body = JSON.parse(rawBuffer.toString('utf8'));
      } catch (err) {
        body = {};
      }
    } else {
      try {
        body = JSON.parse(e.body);
      } catch (err) {
        body = {};
      }
    }
  } else if (e.body && typeof e.body === 'object') {
    body = e.body;
  }
  if (!body || typeof body !== 'object') {
    body = {};
  }
  return {
    action: String(body.action || query.action || e.action || ''),
    body: body,
    query: query,
    headers: headers,
    rawBuffer: rawBuffer,
  };
}

function ok(data) {
  return { code: 0, message: 'ok', data: data };
}

function fail(code, message) {
  return { code: code, message: message, data: null };
}

/** cloudPath 必须落在允许的前缀里，且不许有 .. 越界 */
function safeCloudPath(value) {
  const s = decodeURIComponent(String(value || '').trim());
  if (!s || s.indexOf('..') !== -1 || s.charAt(0) === '/') {
    return '';
  }
  const allowed = ALLOW_PREFIXES.some(one => s.indexOf(one) === 0);
  return allowed ? s : '';
}

function resolveBuffer(parsed) {
  const body = parsed.body;
  if (body.content && String(body.encoding || '').toLowerCase() === 'base64') {
    return Buffer.from(String(body.content), 'base64');
  }
  if (parsed.rawBuffer && parsed.rawBuffer.length) {
    return parsed.rawBuffer;
  }
  if (body.content !== undefined && body.content !== null) {
    return Buffer.from(String(body.content), 'utf8');
  }
  return null;
}

/** 上传完立刻换一次 URL，公共读目录会拿到不带签名的永久地址 */
async function permanentUrl(fileID) {
  try {
    const res = await uniCloud.getTempFileURL({ fileList: [fileID] });
    const one = (res && res.fileList && res.fileList[0]) || {};
    return one.tempFileURL || one.download_url || '';
  } catch (err) {
    console.error('[upload] 取 URL 失败:', fileID, err);
    return '';
  }
}

async function put(parsed) {
  const cloudPath = safeCloudPath(parsed.body.cloudPath || parsed.query.cloudPath);
  if (!cloudPath) {
    return fail(400, 'cloudPath 不合法，必须以 ' + ALLOW_PREFIXES[0] + ' 开头');
  }
  const buf = resolveBuffer(parsed);
  if (!buf || !buf.length) {
    return fail(400, '内容为空，无法上传');
  }
  if (buf.length > MAX_BYTES) {
    return fail(400, '文件超过 ' + Math.floor(MAX_BYTES / 1048576) + 'MB，请先用面板压缩');
  }
  const contentType = String(parsed.body.contentType || parsed.query.contentType || '').trim();

  const options = { cloudPath: cloudPath, fileContent: buf };
  if (contentType) {
    options.contentType = contentType;
  }
  const res = await uniCloud.uploadFile(options);
  const fileID = res && (res.fileID || res.FileID);
  if (!fileID) {
    return fail(500, '上传失败，云存储未返回 fileID');
  }
  const url = (res.downloadUrl || res.tempFileURL || '') || await permanentUrl(fileID);
  return ok({
    fileID: fileID,
    url: url,
    cloudPath: cloudPath,
    size: buf.length,
    md5: crypto.createHash('md5').update(buf).digest('hex'),
  });
}

/** 只查存在性与地址，不重复上传大文件 */
async function head(parsed) {
  const cloudPath = safeCloudPath(parsed.body.cloudPath || parsed.query.cloudPath);
  if (!cloudPath) {
    return fail(400, 'cloudPath 不合法');
  }
  const res = await uniCloud.getTempFileURL({ fileList: [cloudPath] });
  const one = (res && res.fileList && res.fileList[0]) || {};
  return ok({
    cloudPath: cloudPath,
    url: one.tempFileURL || '',
    code: one.code || '',
    msg: one.msg || '',
    exists: Boolean(one.tempFileURL) && one.code !== 'ENOENT',
  });
}

async function deleteFiles(parsed) {
  const list = parsed.body.fileList || [];
  const fileIds = (Array.isArray(list) ? list : [list]).map(one => String(one)).filter(Boolean);
  if (!fileIds.length) {
    return fail(400, '缺少 fileList');
  }
  const res = await uniCloud.deleteFile({ fileList: fileIds });
  return ok({ requested: fileIds.length, fileList: (res && res.fileList) || [] });
}

exports.main = async (event) => {
  const parsed = parseEvent(event);
  if (!parsed.action) {
    return fail(400, '缺少 action');
  }
  if (parsed.action === 'ping') {
    return ok({
      pong: Date.now(),
      fn: 'function-jy-upload',
      // 带版本号，run.py cloud check 才能判断线上这个函数是不是落后了
      version: pkg.version,
      adminConfigured: Boolean(admin.expectedToken()),
    });
  }
  const denied = admin.requireAdmin(parsed.body, parsed.query, parsed.headers);
  if (denied) {
    return denied;
  }
  try {
    if (parsed.action === 'put') {
      return await put(parsed);
    }
    if (parsed.action === 'head') {
      return await head(parsed);
    }
    if (parsed.action === 'delete') {
      return await deleteFiles(parsed);
    }
    return fail(404, '未知 action，可用: ping, put, head, delete');
  } catch (err) {
    console.error('[upload] 执行失败:', parsed.action, err);
    return fail(500, String((err && err.message) || err));
  }
};

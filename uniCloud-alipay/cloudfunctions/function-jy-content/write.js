'use strict';

/**
 * 写接口：只给本地 Python 面板调用，进 index.js 前必过 requireAdmin。
 *
 * 三条纪律：
 *   1. 一律 doc(id).set() 幂等覆盖，重跑同一批不会写出重复数据；
 *   2. 单次调用最多 300 条，面板负责切片，避免顶到请求体上限；
 *   3. 正文与音频不进库，库里只存 URL，所以这里从不接收大内容。
 */

const {
  COLLECTIONS, META_ID, db, putDoc, pick, compact, numOr,
  EPISODE_FIELDS, EPISODE_EXTRA_FIELDS, COLUMN_FIELDS, ACCOUNT_FIELDS,
  ensureMeta, getMeta, countOf, docsByIds, DEFAULT_APP, DEFAULT_SETTINGS,
} = require('./lib.js');

const MAX_ITEMS_PER_CALL = 300;

/** URL 必须是 https，小程序白名单只认 https，本地 127.0.0.1 预览地址一律拒收 */
function isPublicUrl(value) {
  const s = String(value || '');
  return /^https:\/\//.test(s);
}

async function ensureCollections() {
  const created = [];
  const skipped = [];
  const names = [
    COLLECTIONS.accounts, COLLECTIONS.columns, COLLECTIONS.episodes, COLLECTIONS.meta,
    COLLECTIONS.releases,
  ];
  for (const name of names) {
    try {
      await db().createCollection(name);
      created.push(name);
    } catch (err) {
      // 已存在时支付宝云会抛错，能查到数量就算正常
      skipped.push(name);
    }
  }
  return { created: created, skipped: skipped };
}

async function initSite(payload) {
  const collections = await ensureCollections();
  const meta = await ensureMeta();
  // 首次初始化允许面板带上站点名等信息
  if (payload && payload.app) {
    const app = Object.assign({}, DEFAULT_APP, compact(payload.app));
    await db().collection(COLLECTIONS.meta).doc(META_ID).update({ app: app });
  }
  return {
    collections: collections,
    meta: { _id: META_ID, dataVersion: numOr(meta.dataVersion, 0) },
  };
}

function normalizeEpisode(row) {
  const id = String(row.id || row._id || row.awemeId || '');
  if (!id) {
    return null;
  }
  const doc = pick(row, EPISODE_FIELDS.concat(EPISODE_EXTRA_FIELDS));
  doc._id = id;
  doc.id = id;
  doc.awemeId = String(row.awemeId || id);
  doc.title = String(row.title || '').slice(0, 300);
  doc.sort = numOr(row.sort, 0);
  doc.episodeNo = numOr(row.episodeNo, 0);
  doc.episodeTotal = numOr(row.episodeTotal, 0);
  doc.duration = numOr(row.duration, 0);
  doc.enabled = row.enabled !== false && row.enabled !== 'false';
  // 只保留云上可用的 https 地址，本地预览地址不覆盖线上已有值
  if (isPublicUrl(row.audioUrl)) {
    doc.audioUrl = row.audioUrl;
  } else {
    delete doc.audioUrl;
  }
  if (isPublicUrl(row.articleUrl)) {
    doc.articleUrl = row.articleUrl;
  } else {
    delete doc.articleUrl;
  }
  if (!isPublicUrl(doc.cover)) {
    delete doc.cover;
  }
  return compact(doc);
}

async function upsertEpisodes(payload) {
  const rows = (payload.items || []).map(normalizeEpisode).filter(Boolean);
  if (rows.length > MAX_ITEMS_PER_CALL) {
    return { code: 400, message: '单次最多 ' + MAX_ITEMS_PER_CALL + ' 条，请分批' };
  }
  const written = [];
  for (const row of rows) {
    await putDoc(COLLECTIONS.episodes, row._id, row);
    written.push(row._id);
  }
  return { written: written.length, ids: written };
}

function normalizeColumn(row) {
  const id = String(row.id || row._id || '');
  if (!id) {
    return null;
  }
  const doc = pick(row, COLUMN_FIELDS);
  doc._id = id;
  doc.id = id;
  doc.name = String(row.name || '').slice(0, 200);
  doc.sort = numOr(row.sort, 0);
  doc.episodeTotal = numOr(row.episodeTotal, 0);
  // episodeIds 决定线上集序，面板按集号排好再传
  const ids = (row.episodeIds || []).map(one => String(one)).filter(Boolean);
  doc.episodeIds = Array.from(new Set(ids));
  doc.nEpisodes = numOr(row.nEpisodes, doc.episodeIds.length);
  if (!isPublicUrl(doc.cover)) {
    delete doc.cover;
  }
  return compact(doc);
}

async function upsertColumns(payload) {
  const rows = (payload.items || []).map(normalizeColumn).filter(Boolean);
  if (rows.length > MAX_ITEMS_PER_CALL) {
    return { code: 400, message: '单次最多 ' + MAX_ITEMS_PER_CALL + ' 条，请分批' };
  }
  const written = [];
  for (const row of rows) {
    await putDoc(COLLECTIONS.columns, row._id, row);
    written.push(row._id);
  }
  return { written: written.length, ids: written };
}

function normalizeAccount(row) {
  const id = String(row.id || row._id || row.slug || '');
  if (!id) {
    return null;
  }
  const doc = pick(row, ACCOUNT_FIELDS);
  doc._id = id;
  doc.id = id;
  doc.slug = String(row.slug || id);
  doc.name = String(row.name || '').slice(0, 100);
  doc.enabled = row.enabled !== false && row.enabled !== 'false';
  doc.maxItems = numOr(row.maxItems, 0);
  doc.awemeCount = numOr(row.awemeCount, 0);
  doc.scannedItems = numOr(row.scannedItems, 0);
  return compact(doc);
}

async function upsertAccounts(payload) {
  const rows = (payload.items || []).map(normalizeAccount).filter(Boolean);
  if (rows.length > MAX_ITEMS_PER_CALL) {
    return { code: 400, message: '单次最多 ' + MAX_ITEMS_PER_CALL + ' 条，请分批' };
  }
  const written = [];
  for (const row of rows) {
    await putDoc(COLLECTIONS.accounts, row._id, row);
    written.push(row._id);
  }
  return { written: written.length, ids: written };
}

/** 下架：默认只从栏目里摘掉引用并置 enabled=false，purgeFiles 才连云存储文件一起删 */
async function deleteEpisodes(payload) {
  const ids = (payload.ids || []).map(one => String(one)).filter(Boolean);
  if (!ids.length) {
    return { code: 400, message: '缺少 ids' };
  }
  if (ids.length > MAX_ITEMS_PER_CALL) {
    return { code: 400, message: '单次最多 ' + MAX_ITEMS_PER_CALL + ' 条，请分批' };
  }
  const purge = payload.purgeFiles === true || payload.purgeFiles === 'true';
  const removedFiles = [];
  const result = { removed: ids.length, filesRemoved: 0 };

  // 运行时不一定认 in，批量取交给 lib 兜底
  const docs = (await docsByIds(COLLECTIONS.episodes, ids)).rows;

  if (purge) {
    const fileIds = [];
    docs.forEach((doc) => {
      if (doc.audioFileId) fileIds.push(doc.audioFileId);
      if (doc.articleFileId) fileIds.push(doc.articleFileId);
    });
    if (fileIds.length) {
      try {
        const del = await uniCloud.deleteFile({ fileList: fileIds });
        result.filesRemoved = ((del && del.fileList) || []).length || fileIds.length;
        fileIds.forEach(one => removedFiles.push(one));
      } catch (err) {
        result.fileError = String(err && err.message || err);
      }
    }
    for (const doc of docs) {
      await db().collection(COLLECTIONS.episodes).doc(doc._id).remove();
    }
    const cols = await db().collection(COLLECTIONS.columns).limit(500).get();
    for (const col of (cols.data || [])) {
      const kept = (col.episodeIds || []).filter(one => ids.indexOf(String(one)) === -1);
      if (kept.length !== (col.episodeIds || []).length) {
        await db().collection(COLLECTIONS.columns).doc(col._id).update({
          episodeIds: kept,
          nEpisodes: kept.length,
        });
      }
    }
  } else {
    for (const id of ids) {
      await db().collection(COLLECTIONS.episodes).doc(id).update({ enabled: false });
    }
    const cols = await db().collection(COLLECTIONS.columns).limit(500).get();
    for (const col of (cols.data || [])) {
      const kept = (col.episodeIds || []).filter(one => ids.indexOf(String(one)) === -1);
      if (kept.length !== (col.episodeIds || []).length) {
        await db().collection(COLLECTIONS.columns).doc(col._id).update({
          episodeIds: kept,
          nEpisodes: kept.length,
        });
      }
    }
  }
  result.removedFiles = removedFiles;
  return result;
}

/** 线上只留最后一次发布记录，回滚靠本地 sqlite + 重新 push */
async function pushRelease(payload) {
  const meta = await ensureMeta();
  const version = numOr(meta.dataVersion, 0) + 1;
  const now = Date.now();
  const counts = {
    episodes: await countOf(COLLECTIONS.episodes),
    columns: await countOf(COLLECTIONS.columns),
    accounts: await countOf(COLLECTIONS.accounts),
  };
  const record = {
    _id: 'rel-' + now,
    dataVersion: version,
    releasedAt: now,
    note: String((payload && payload.note) || '').slice(0, 200),
    counts: counts,
    episodes: numOr(payload && payload.episodes, 0),
    columns: numOr(payload && payload.columns, 0),
  };
  await db().collection(COLLECTIONS.releases).doc(record._id).set(record);
  await db().collection(COLLECTIONS.meta).doc(META_ID).update({
    dataVersion: version,
    updatedAt: now,
    counts: counts,
    lastRelease: record._id,
  });
  return { dataVersion: version, releasedAt: now, counts: counts };
}

/** 面板「线上状态」卡片用：版本、各集合条数、最近一次发布 */
async function remoteStats() {
  const meta = await getMeta();
  const recent = await db().collection(COLLECTIONS.releases)
    .orderBy('releasedAt', 'desc')
    .limit(5)
    .get();
  return {
    app: (meta && meta.app) || DEFAULT_APP,
    settings: (meta && meta.settings) || DEFAULT_SETTINGS,
    dataVersion: numOr(meta && meta.dataVersion, 0),
    updatedAt: numOr(meta && meta.updatedAt, 0),
    counts: {
      episodes: await countOf(COLLECTIONS.episodes),
      columns: await countOf(COLLECTIONS.columns),
      accounts: await countOf(COLLECTIONS.accounts),
      releases: await countOf(COLLECTIONS.releases),
    },
    recent: (recent.data || []).map(one => ({
      _id: one._id,
      dataVersion: one.dataVersion,
      releasedAt: one.releasedAt,
      note: one.note,
      counts: one.counts,
    })),
  };
}

/** 改站点名/自动连播等全局设置 */
async function updateSettings(payload) {
  await ensureMeta();
  const patch = {};
  if (payload.app) patch.app = Object.assign({}, DEFAULT_APP, compact(payload.app));
  if (payload.settings) {
    patch.settings = Object.assign({}, DEFAULT_SETTINGS, compact(payload.settings));
  }
  if (!Object.keys(patch).length) {
    return { code: 400, message: '没有要更新的字段' };
  }
  patch.updatedAt = Date.now();
  await db().collection(COLLECTIONS.meta).doc(META_ID).update(patch);
  return { updated: Object.keys(patch) };
}

module.exports = {
  MAX_ITEMS_PER_CALL,
  initSite,
  upsertEpisodes,
  upsertColumns,
  upsertAccounts,
  deleteEpisodes,
  pushRelease,
  remoteStats,
  updateSettings,
  ensureCollections,
};

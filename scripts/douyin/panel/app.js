/* 抖音采集流水线面板前端逻辑（无构建，原生 JS） */
const $ = (s, r = document) => r.querySelector(s)
const $$ = (s, r = document) => [...r.querySelectorAll(s)]
function el(tag, cls, html) {
  const n = document.createElement(tag)
  if (cls)
    n.className = cls
  if (html != null)
    n.innerHTML = html
  return n
}
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
}

const S = {
  accounts: [],
  columns: [],
  jobs: [],
  videos: [],
  total: 0,
  selected: new Set(),
  offset: 0,
  limit: 120,
  steps: ['audio', 'transcript', 'article'],
  busy: null,
  cookieState: null,
  detail: null,
  view: 'queue',
}

/* ---------- 网络 ---------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  const text = await res.text()
  let data = null
  try { data = text ? JSON.parse(text) : null }
  catch { data = { detail: text } }
  if (!res.ok) {
    const msg = (data && (data.detail || data.message)) || `HTTP ${res.status}`
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}

function fmtDur(ms) {
  const t = Math.max(0, Math.round(ms / 1000))
  const m = Math.floor(t / 60); const sec = t % 60
  return m > 59
    ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    : `${m}:${String(sec).padStart(2, '0')}`
}

function toast(msg, kind = '') {
  const t = el('div', `toast ${kind}`, esc(msg))
  $('#toasts').appendChild(t)
  setTimeout(() => t.remove(), kind === 'err' ? 9000 : 4200)
}

async function guard(fn) {
  try { await fn() }
  catch (e) { toast(e.message || String(e), 'err') }
}

/* ---------- 顶栏 / 概览 ---------- */
const STAGE_CN = {
  scanned: '已入库',
  audio: '已下载',
  transcript: '已转写',
  article: '已成文',
  published: '已上架',
  failed: '失败',
  skipped: '跳过',
}

function renderChips(st) {
  const c = [
    ['账号', st.accounts],
    ['作品', st.videos],
    ['栏目', st.columns],
    ['时长', `${st.total_hours}h`],
    ['ASR', `${Math.round((st.asr_seconds || 0) / 60)}min`],
  ]
  $('#chips').innerHTML = c.map(([k, v]) => `${k} <b>${v}</b>`)
    .map((x, i) => `<span class="chip">${x}</span>`)
    .join('')
  const s = st.stages || {}
  const extra = Object.entries(s).map(([k, v]) =>
    `<span class="chip">${STAGE_CN[k] || k} <b>${v.n}</b></span>`).join('')
  $('#chips').insertAdjacentHTML('beforeend', extra)
  const done = ['article', 'published', 'skipped'].reduce((a, k) => a + ((s[k] || {}).n || 0), 0)
  $('#pendingN').textContent = Math.max(0, (st.videos || 0) - done)
}

function renderState(ov) {
  const run = ov.process || {}
  const busyJob = (ov.jobs || []).find(j => j.status === 'running')
  const box = $('#runstate')
  box.className = 'runstate'
  let txt = '空闲'
  if (run.running) {
    box.classList.add('busy')
    txt = `处理 ${run.done}/${run.total} · ${STAGE_CN[run.step] || run.step || ''} ${run.current ? run.current.slice(-6) : ''}`
  }
  else if (busyJob) {
    box.classList.add('busy')
    txt = `${KIND_CN[busyJob.kind] || busyJob.kind} 进行中 ${busyJob.elapsed}s`
  }
  const err = (ov.jobs || []).find(j => j.status === 'error' && Date.now() - (j.ts || 0) < 60000)
  if (!run.running && !busyJob && ov.lastError) { box.classList.add('err'); txt = '有任务失败' }
  $('#runtext').textContent = txt
  $('#btnStop').disabled = !(run.running || busyJob)
  const jobs = ov.jobs || []
  const lastErr = jobs.find(j => j.status === 'error')
  S.lastError = lastErr && lastErr.error ? lastErr.error : ''
  if (run.running && run.note)
    $('#procInfo').textContent = run.note
  else if (busyJob)
    $('#procInfo').textContent = busyJob.note || busyJob.kind
  else $('#procInfo').textContent = ov.process && ov.process.note ? ov.process.note : ''
}

/* ---------- 登录态 ---------- */
function renderCookie(ov) {
  const ck = ov.cookie || {}
  const lg = ov.browser || {}
  const box = $('#cookieLight')
  let cls = 'err'; let t1 = '没有 Cookie'; let t2 = '粘贴 Cookie 或点扫码登录'
  if (ck.has_sessionid) {
    cls = 'warn'
    t1 = '已有 sessionid，但未验证能否翻页'
    t2 = `文件 ${ck.length} 字符 · ${ck.file}`
  }
  if (S.cookieState && S.cookieState.checked) {
    const r = S.cookieState
    cls = r.ok ? 'ok' : (r.logged_in ? 'warn' : 'err')
    t1 = r.ok ? '登录态可用，能正常翻页' : (r.logged_in ? '登录态异常' : '未登录')
    t2 = r.message || ''
    if (r.nickname)
      t1 = `${r.nickname} · ${t1}`
    if (r.aweme_count)
      t2 += `（主页 ${r.aweme_count} 条）`
  }
  box.className = `light ${cls}`
  box.innerHTML = `<i class="lamp"></i><div class="txt"><div class="t1">${esc(t1)}</div><div class="t2">${esc(t2)}</div></div>`
  $('#engineTag').textContent = `引擎 ${ov.engine || ''}`
}

/* ---------- 账号 ---------- */
function renderAccounts() {
  const list = $('#acctList')
  list.innerHTML = ''
  S.accounts.forEach((a) => {
    const row = el('div', `acct${a.enabled ? '' : ' off'}`)
    const pct = a.aweme_count ? Math.min(100, Math.round((a.item_count / a.aweme_count) * 100)) : 0
    row.innerHTML = `
      <div class="top">
        <span class="nm">${esc(a.name || a.slug)}</span>
        <span class="slug">${esc(a.slug || '')}</span>
        <span class="acts">
          <button class="btn sm" data-act="scan" title="只采集这个账号">采集</button>
          <button class="btn sm" data-act="edit" title="编辑">改</button>
          <button class="btn sm" data-act="del" title="删除">删</button>
        </span>
      </div>
      <div class="meta">入库 ${a.item_count}${a.aweme_count ? ` / 主页 ${a.aweme_count}（${pct}%）` : ''} · 已处理 ${a.processed_count} · 栏目 ${a.column_count} · 上限 ${a.max_items || '不限'}</div>
      <div class="note">${esc(a.scan_note || '未扫描')} ${a.last_scan_at ? `· ${esc(a.last_scan_at)}` : ''}</div>`
    const edit = el('div', 'edit')
    edit.innerHTML = `
      <div class="grid2">
        <div class="field"><label>名称</label><input type="text" data-f="name" value="${esc(a.name || '')}"></div>
        <div class="field"><label>抓取上限（0=全部）</label><input type="number" data-f="max_items" value="${a.max_items || 0}" min="0"></div>
      </div>
      <div class="field"><label>定位 / 风格</label><input type="text" data-f="style" value="${esc(a.style || '')}"></div>
      <div class="row"><label class="check"><input type="checkbox" data-f="enabled" ${a.enabled ? 'checked' : ''}>启用</label>
      <span class="spacer" style="flex:1"></span>
      <button class="btn sm" data-act="save">保存</button></div>`
    row.appendChild(edit)
    row.addEventListener('click', (ev) => {
      const act = ev.target.dataset && ev.target.dataset.act
      if (!act)
        return
      ev.stopPropagation()
      if (act === 'edit')
        edit.classList.toggle('on')
      if (act === 'scan')
        startScan([a.sec_user_id], a.name)
      if (act === 'del') {
        guard(async () => {
          if (!confirm(`删除账号「${a.name}」？数据库里已入库的作品不会删除。`))
            return
          await api(`/api/accounts/${a.sec_user_id}`, { method: 'DELETE' })
          toast('已删除', 'ok'); refresh()
        })
      }
      if (act === 'save') {
        guard(async () => {
          const body = { enabled: edit.querySelector('[data-f=enabled]').checked };
          ['name', 'style'].forEach((k) => { body[k] = edit.querySelector(`[data-f=${k}]`).value.trim() })
          body.max_items = Number(edit.querySelector('[data-f=max_items]').value) || 0
          await api(`/api/accounts/${a.sec_user_id}`, { method: 'PUT', body })
          edit.classList.remove('on'); toast('已保存', 'ok'); refresh()
        })
      }
    })
    list.appendChild(row)
  })
  $('#acctN').textContent = `${S.accounts.length} 个`
  fillSelects()
}

function fillSelects() {
  const opts = ['fAcct', 'cbAcct', 'pubAcct']
  opts.forEach((id) => {
    const sel = $(`#${id}`)
    if (!sel)
      return
    const keep = sel.value
    const first = id === 'fAcct' ? '<option value="">全部账号</option>' : '<option value="">选择账号…</option>'
    sel.innerHTML = first + S.accounts.map(a =>
      `<option value="${a.sec_user_id}">${esc(a.name || a.slug)}</option>`).join('')
    sel.value = keep
  })
  const colOpts = ['fCol', 'pubCol']
  colOpts.forEach((id) => {
    const sel = $(`#${id}`)
    if (!sel)
      return
    const keep = sel.value
    const first = id === 'fCol'
      ? '<option value="">全部栏目</option><option value="__none__">未归类</option>'
      : '<option value="">全部栏目</option>'
    sel.innerHTML = first + S.columns.map(c =>
      `<option value="${c.column_id}">${esc(c.name)}（${c.n_videos}）</option>`).join('')
    sel.value = keep
  })
}

/* ---------- 队列 ---------- */
function renderQueue() {
  const tb = $('#qBody')
  tb.innerHTML = ''
  const colById = Object.fromEntries(S.columns.map(c => [c.column_id, c]))
  S.videos.forEach((v) => {
    const tr = el('tr')
    if (S.selected.has(v.aweme_id))
      tr.className = 'sel'
    const dur = v.duration_ms ? fmtDur(v.duration_ms) : '—'
    const col = v.column_id ? (colById[v.column_id] || {}).name || v.column_name : ''
    tr.innerHTML = `
      <td><input type="checkbox" style="width:13px;height:13px;accent-color:var(--accent)" ${S.selected.has(v.aweme_id) ? 'checked' : ''}></td>
      <td class="t" title="${esc(v.title)}">${esc(v.title)}${v.kind === 'image_text' ? ' <span class="tag">图文</span>' : ''}</td>
      <td class="t" title="${esc(v.account_name || '')}">${esc(v.account_name || '')}</td>
      <td class="t">${col ? `<span class="tag mix">${esc(col)}</span>` : (v.mix_name ? `<span class="tag">${esc(v.mix_name)}</span>` : '<span style="color:var(--ink3)">—</span>')}</td>
      <td class="num">${v.episode_no || v.ep_no || ''}</td>
      <td class="num">${dur}</td>
      <td><span class="st ${esc(v.stage)}"><i></i>${STAGE_CN[v.stage] || v.stage}${v.error ? ' ⚠' : ''}</span>${v.offline ? ' <span class="tag">已下架</span>' : ''}</td>
      <td><div class="row" style="gap:4px;flex-wrap:nowrap">
        <button class="btn sm" data-act="open">看</button>
        <button class="btn sm" data-act="one">跑</button>
      </div></td>`
    const ck = tr.querySelector('input')
    ck.addEventListener('change', () => {
      if (ck.checked)
        S.selected.add(v.aweme_id); else S.selected.delete(v.aweme_id)
      tr.classList.toggle('sel', ck.checked); updateSelInfo()
    })
    tr.addEventListener('click', (ev) => {
      if (ev.target.tagName === 'INPUT')
        return
      const act = ev.target.dataset && ev.target.dataset.act
      if (act === 'open')
        openDetail(v.aweme_id)
      else if (act === 'one')
        startProcess([v.aweme_id], v.title)
      else if (!act)
        openDetail(v.aweme_id)
    })
    tb.appendChild(tr)
  })
  $('#qEmpty').style.display = S.videos.length ? 'none' : ''
  $('#qN').textContent = S.total
  $('#pageInfo').textContent = S.total ? `${S.offset + 1}-${Math.min(S.offset + S.limit, S.total)} / ${S.total}` : '0'
  $('#btnPrev').disabled = S.offset <= 0
  $('#btnNext').disabled = S.offset + S.limit >= S.total
  updateSelInfo()
}

function updateSelInfo() {
  $('#selInfo').textContent = S.selected.size ? `已选 ${S.selected.size} 条` : ''
  const sh = $('#selHint')
  if (sh)
    sh.textContent = S.selected.size ? `已勾选 ${S.selected.size} 条` : '未勾选条目（去「作品队列」勾选）'
  const boxes = $$('#qBody input[type=checkbox]')
  $('#ckAll').checked = boxes.length > 0 && boxes.every(b => b.checked)
}

async function loadQueue() {
  const p = new URLSearchParams({
    sec_user_id: $('#fAcct').value || '',
    column_id: $('#fCol').value || '',
    stage: $('#fStage').value || '',
    q: $('#fQ').value.trim() || '',
    order: $('#fOrder').value || 'create_time',
    limit: S.limit,
    offset: S.offset,
  })
  const res = await api(`/api/videos?${p}`)
  S.videos = res.items; S.total = res.total; renderQueue()
}

/* ---------- 任务 ---------- */
const KIND_CN = {
  'scan': '采集',
  'process': '处理',
  'compact': '音频瘦身',
  'columns': 'AI 分栏目',
  'probe': '账号试探',
  'check': '登录体检',
  'login': '扫码登录',
  'cloud-publish': '云端上架',
  'cloud-sync': '元数据同步',
  'cloud-cover': '封面转存',
  'publish': '云端上架',
}

function renderJobs() {
  const box = $('#jobList')
  if (!S.jobs.length) { box.innerHTML = '<div class="empty" style="padding:18px">暂无任务</div>'; return }
  box.innerHTML = ''
  S.jobs.slice(0, 8).forEach((j) => {
    const line = el('div', `job ${j.status}`)
    const msg = j.status === 'error' ? j.error : (j.note || (j.result ? JSON.stringify(j.result).slice(0, 160) : ''))
    line.innerHTML = `<span class="k">${esc(KIND_CN[j.kind] || j.kind)}</span><span class="m">${esc(msg || j.status)}</span><span class="t">${j.elapsed}s</span>`
    box.appendChild(line)
  })
}

function startScan(secs, label) {
  guard(async () => {
    const r = await api('/api/scan', { method: 'POST', body: { sec_user_ids: secs } })
    toast(`已开始采集 ${label || `${r.accounts.length} 个账号`}；Chrome 会自己滚动，别关掉窗口`, 'ok')
    refresh()
  })
}

function startProcess(ids, label) {
  guard(async () => {
    const r = await api('/api/process', {
      method: 'POST',
      body: { ids, steps: S.steps, force: false },
    })
    toast(`开始处理 ${r.queued} 条：${S.steps.map(s => ({ audio: '音频', transcript: '转写', article: '文章' }[s])).join(' → ')}`, 'ok')
    refresh()
  })
}

/* ---------- 详情抽屉 ---------- */
function mdToHtml(src) {
  const out = []
  let list = null
  const inline = t => esc(t)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
  String(src || '').split(/\r?\n/).forEach((raw) => {
    const line = raw.trim()
    if (!line) { if (list) { out.push('</ul>'); list = null } return }
    if (/^#{1,6}\s/.test(line)) {
      if (list) { out.push('</ul>'); list = null }
      const lvl = line.match(/^#+/)[0].length
      const tag = lvl <= 2 ? 'h2' : 'h3'
      out.push(`<${tag}>${inline(line.replace(/^#+\s*/, ''))}</${tag}>`)
    }
    else if (/^([-*•]|\d+[.、])\s+/.test(line)) {
      if (!list) { out.push('<ul>'); list = 1 }
      out.push(`<li>${inline(line.replace(/^([-*•]|\d+[.、])\s+/, ''))}</li>`)
    }
    else if (/^>\s?/.test(line)) {
      if (list) { out.push('</ul>'); list = null }
      out.push(`<blockquote>${inline(line.replace(/^>\s?/, ''))}</blockquote>`)
    }
    else if (/^(-{3,}|\*{3,})\s*$/.test(line)) {
      if (list) { out.push('</ul>'); list = null }
      out.push('<hr>')
    }
    else {
      if (list) { out.push('</ul>'); list = null }
      out.push(`<p>${inline(line)}</p>`)
    }
  })
  if (list)
    out.push('</ul>')
  return out.join('\n')
}

async function openDetail(id) {
  guard(async () => {
    const v = await api(`/api/videos/${id}`)
    S.detail = v
    $('#drawer').classList.add('on')
    $('#dTitle').textContent = v.title || id
    $('#dStage').innerHTML = `<span class="st ${esc(v.stage)}"><i></i>${STAGE_CN[v.stage] || v.stage}</span>`
    $('#dSrc').href = v.source_url || `https://www.douyin.com/video/${id}`
    $('#dPlay').disabled = !v.audio_path
    $('#dPub').disabled = !v.article_path
    const head = `
      <h1>${esc(v.title)}</h1>
      <div class="sub">${esc(v.account_name || '')}${v.column_name ? ` · 栏目「${esc(v.column_name)}」` : ''}
        ${v.episode_no ? ` · 第 ${v.episode_no} 集` : ''}${v.create_time ? ` · ${new Date(v.create_time * 1000).toLocaleDateString('zh-CN')}` : ''}</div>`
    if (v.article_md) {
      $('#dBody').innerHTML = `${head}<div class="md">${mdToHtml(v.article_md)}</div>
        <div class="kv">aweme_id ${esc(v.aweme_id)} · 转写 ${(v.transcript_txt || '').length} 字 · 文章 ${(v.article_md || '').length} 字</div>`
    }
    else if (v.transcript_txt) {
      $('#dBody').innerHTML = `${head}<div class="md"><p style="color:var(--ink3)">还没有生成文章，下面是转写原文：</p>${mdToHtml(v.transcript_txt)}</div>`
    }
    else {
      $('#dBody').innerHTML = `${head}<div class="md"><p style="color:var(--ink3)">这条作品还什么都没跑。描述：<br>${esc((v.desc_raw || '').slice(0, 400))}</p></div>`
    }
    $('#dBody').scrollTop = 0
    $('.drawer .scroll').scrollTop = 0
    const au = $('#audio')
    au.pause()
    $('#dPlayer').style.display = 'none'
    au.src = v.audio_path ? `/media/audio?aweme_id=${v.aweme_id}` : ''
  })
}

/* ---------- 栏目视图 ---------- */
function renderColumns() {
  const box = $('#colList')
  $('#cN').textContent = S.columns.length
  if (!S.columns.length) {
    box.innerHTML = '<div class="empty" style="grid-column:1/-1">还没有栏目。先在「作品队列」采集，再点上面的「AI 自动分栏目」。</div>'
    return
  }
  const accById = Object.fromEntries(S.accounts.map(a => [a.sec_user_id, a]))
  box.innerHTML = ''
  S.columns.forEach((c) => {
    const pct = c.n_videos ? Math.round((c.n_articles / c.n_videos) * 100) : 0
    const card = el('div', 'col')
    card.innerHTML = `
      <div class="nm">${esc(c.name)} ${c.locked ? '<span class="lock">已锁定</span>' : ''}</div>
      <div class="slug">${esc(c.slug || '')} · ${esc((accById[c.sec_user_id] || {}).name || '')} · ${esc(c.source || '')}</div>
      <div class="meta">${c.n_videos} 集 · 已成文 ${c.n_articles} · 已上架 ${c.n_published || 0}${c.n_offline ? ` · <span style="color:var(--warn,#b06a1f)">已下架 ${c.n_offline}</span>` : ''} · ${c.total_ms ? Math.round(c.total_ms / 360000) / 10 : 0} 小时</div>
      ${c.description ? `<div class="meta" style="color:var(--ink3)">${esc(c.description)}</div>` : ''}
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="acts">
        <button class="btn sm" data-act="run">处理全栏目</button>
        <button class="btn sm" data-act="pub" ${c.n_articles ? `` : `'disabled'`}>上架全栏目</button>
        <button class="btn sm" data-act="off" ${c.n_published ? `` : `'disabled'`}>下架全栏目</button>
        <button class="btn sm" data-act="back" ${c.n_offline ? `` : `'disabled'`}>重新上架(${c.n_offline || 0})</button>
        <button class="btn sm" data-act="queue">看作品</button>
        <button class="btn sm" data-act="edit">改</button>
        <button class="btn sm" data-act="lock">${c.locked ? '解锁' : '锁定'}</button>
      </div>`
    card.addEventListener('click', (ev) => {
      const act = ev.target.dataset && ev.target.dataset.act
      if (!act)
        return
      if (act === 'run') {
        guard(async () => {
          await api('/api/process', { method: 'POST', body: { column_id: c.column_id, steps: S.steps } })
          toast(`已排入处理队列：${c.name}`, 'ok'); refresh()
        })
      }
      if (act === 'pub') {
        guard(async () => {
          if (!confirm(`上架「${c.name}」全部已成文条目？小程序那边立刻能拉到。`))
            return
          const r = await api('/api/cloud/publish', {
            method: 'POST',
            body: { column_id: c.column_id, note: `栏目卡片上架：${c.name}` },
          })
          toast(`上架任务已排队：${c.name} · 进度看左下任务`, 'ok'); refresh()
        })
      }
      if (act === 'off') {
        guard(async () => {
          if (!confirm(`下架「${c.name}」？线上 ${c.n_published || 0} 集会立刻看不到，云存储文件保留。`))
            return
          const r = await api('/api/cloud/offline', {
            method: 'POST',
            body: { column_id: c.column_id, purge_files: false },
          })
          toast(`已下架 ${r.removed} 集：${c.name}`, 'ok'); S.cloudProbe = null; refresh()
        })
      }
      if (act === 'back') {
        guard(async () => {
          if (!confirm(`重新上架「${c.name}」已下架的 ${c.n_offline} 集？`))
            return
          const r = await api('/api/cloud/restore', { method: 'POST', body: { column_id: c.column_id } })
          toast(`已恢复 ${r.restored} 集：${c.name}`, 'ok'); S.cloudProbe = null; refresh()
        })
      }
      if (act === 'queue') { $('#fCol').value = c.column_id; S.offset = 0; showView('queue'); loadQueue() }
      if (act === 'lock') {
        guard(async () => {
          await api(`/api/columns/${c.column_id}`, { method: 'PATCH', body: { locked: c.locked ? 0 : 1 } })
          refresh()
        })
      }
      if (act === 'edit') {
        const name = prompt('栏目名', c.name)
        if (name === null)
          return
        const desc = prompt('栏目简介', c.description || '')
        guard(async () => {
          await api(`/api/columns/${c.column_id}`, { method: 'PATCH', body: { name, description: desc || '', locked: 1 } })
          toast('已保存并锁定（锁定后 AI 不再改动）', 'ok'); refresh()
        })
      }
    })
    box.appendChild(card)
  })
}

/* ---------- 视图切换 ---------- */
function showView(v) {
  S.view = v
  $$('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.view === v))
  $$('.view').forEach(n => n.classList.toggle('on', n.id === `view-${v}`))
  if (v === 'columns')
    renderColumns()
  if (v === 'queue')
    loadQueue().catch(e => toast(e.message, 'err'))
  if (v === 'log')
    loadLog().catch(() => {})
  if (v === 'output')
    refresh(false).catch(() => {})
}

async function loadLog() {
  const rows = await api('/api/events?limit=120')
  $('#logBody').innerHTML = rows.map(r =>
    `<div>${esc(r.ts)} <span style="color:${r.level === 'error' ? 'var(--err)' : r.level === 'warn' ? 'var(--warn)' : 'var(--ink3)'}">[${esc(r.level)}]</span> ${esc(r.scope)} ${esc(r.message)}</div>`).join('')
  || '<div>暂无日志</div>'
}

/* ---------- 音频体积 ---------- */
function fmtBytes(n) {
  n = n || 0
  return n >= 1073741824
    ? `${(n / 1073741824).toFixed(2)} GB`
    : n >= 1048576
      ? `${(n / 1048576).toFixed(1)} MB`
      : `${Math.round(n / 1024)} KB`
}

function _abox(k, v, sub) {
  return `<div style="flex:1 1 118px;border:1px solid var(--line);border-radius:6px;padding:7px 9px">
  <div style="font-size:11px;color:var(--ink3)">${k}</div>
  <div style="font-size:15px;font-weight:600;font-variant-numeric:tabular-nums">${v}</div>
  <div style="font-size:11px;color:var(--ink3)">${sub || ''}</div></div>`
}

function renderAudio(ov) {
  const a = ov.audio
  if (!a)
    return
  const max = Math.max(...Object.values(a.projection), 1)
  // 基准取改造前的双声道 MP3，"相对现状"就是各档位占原体积的比例
  const baseGB = (a.projection.mp3_stereo || max) / 1073741824
  const now = a.bytes || 0
  const light = $('#audioLight')
  const gap = a.cur_kbps && a.kbps ? a.cur_kbps - a.kbps : 0
  light.className = `light ${a.legacy_mp3 ? 'warn' : 'ok'}`
  light.querySelector('.t1').textContent = a.legacy_mp3
    ? `${a.legacy_mp3} 集还是双声道 MP3，点「瘦身存量」转成当前档位`
    : `全部音频已在 ${a.label || a.profile} 档位`
  light.querySelector('.t2').textContent = a.legacy_mp3
    ? `按当前档位转完，本地 ${fmtBytes(now)} 约能降到 ${fmtBytes(Math.round(now * (a.kbps / a.cur_kbps) || 0))}`
    : `新增音频继续按这个档位落库`

  $('#audioStats').innerHTML = [
    _abox('本地已落库', `${a.files} 集`, fmtBytes(now)),
    _abox('平均每集', a.files ? fmtBytes(Math.round(now / a.files)) : '-', `${a.episodes_total} 集总数`),
    _abox('全库时长', `${a.hours_total} 小时`, `已下载 ${a.files_hours || 0} 小时 · 当前 ${a.cur_kbps} kbps`),
    _abox('全库预估', fmtBytes(a.projection[a.profile] || 0), `按 ${a.label || a.profile}`),
  ].join('')

  const sel = $('#audioProfile')
  const sig = a.profiles.map(x => x.name).join(',')
  if (sel.dataset.sig !== sig) {
    sel.innerHTML = a.profiles.map(x =>
      `<option value="${x.name}">${esc(x.label)} · ${x.kbps}kbps · .${x.ext}</option>`).join('')
    sel.dataset.sig = sig
  }
  if (document.activeElement !== sel)
    sel.value = a.profile

  $('#audioTable').innerHTML = `<tr><th>档位</th><th style="width:112px">全库预估</th><th style="width:62px">相对现状</th></tr>${
    a.profiles.map((x) => {
      const gb = (a.projection[x.name] || 0) / 1073741824
      const pct = Math.max(2, Math.round(gb / baseGB * 100))
      const on = x.name === a.profile
      return `<tr${on ? ' style="background:var(--hover,#eef0f2)"' : ''}>
        <td class="t" title="${esc(x.note)}">${on ? '● ' : '○ '}${esc(x.short || x.label)}
          <span style="color:var(--ink3)">${x.kbps}k</span></td>
        <td class="num">${gb.toFixed(2)} GB
          <div style="height:3px;background:var(--line2);border-radius:2px;margin-top:4px;overflow:hidden">
            <i style="display:block;height:100%;width:${pct}%;background:${on ? 'var(--ok)' : 'var(--ink3)'}"></i></div></td>
        <td class="num">${Math.round(gb / baseGB * 100)}%</td></tr>`
    }).join('')}`
}

/* ---------- 云端上架 ---------- */
function _cfill(sel, v) {
  const n = $(sel)
  if (n && document.activeElement !== n)
    n.value = v == null ? '' : v
}

function _crow(k, v, cls) {
  return `<tr><td class="t" style="color:var(--ink3);width:132px">${k}</td><td class="t" style="${cls || ''}">${v}</td></tr>`
}

function renderCloud(ov) {
  const c = (ov && ov.cloud) || {}
  S.cloud = c
  const light = $('#cloudLight')
  let cls = 'err'; let t1 = '云端未配置'; let t2 = '填上云函数地址和管理令牌，再点「测试连接」'
  if (c.base_url && c.token_configured) {
    const covMiss = (c.coverTotal || 0) - (c.covers || 0)
    cls = (c.pending > 0 || covMiss > 0) ? 'warn' : 'ok'
    t1 = c.pending > 0
      ? `配置就绪，还有 ${c.pending} 集成文待推`
      : covMiss > 0
        ? `配置就绪，还有 ${covMiss} 个合集封面没转存`
        : '配置就绪，本地没有待推条目'
    t2 = `空间 ${c.space_id || '-'} · 前缀 ${c.prefix || '-'} · 已推 ${c.pushed} 集`
      + ` · 封面 ${c.covers || 0}/${c.coverTotal || 0}`
  }
  else if (c.base_url) {
    cls = 'warn'
    t1 = '接口地址已填，缺管理令牌'
    t2 = `粘贴 ADMIN_TOKEN，或把它写到 ${c.token_file || 'unicloud.key'}`
  }
  if (S.cloudProbe && S.cloudProbe.kind === 'test') {
    if (S.cloudProbe.ok) { cls = 'ok'; t1 = '云函数联通正常'; t2 = S.cloudProbe.summary || t2 }
    else { cls = 'err'; t1 = '云函数不通'; t2 = S.cloudProbe.summary || '看下面明细' }
  }
  light.className = `light ${cls}`
  light.querySelector('.t1').textContent = t1
  light.querySelector('.t2').textContent = t2

  _cfill('#cfBase', c.base_url)
  _cfill('#cfHost', c.storage_host)
  _cfill('#cfPrefix', c.prefix)
  const tk = $('#cfToken')
  if (document.activeElement !== tk)
    tk.value = ''
  tk.placeholder = c.token_configured ? '已配置，留空保持不变' : '粘贴 ADMIN_TOKEN'
  $('#selHint').textContent = S.selected.size ? `已勾选 ${S.selected.size} 条` : '未勾选条目（去「作品队列」勾选）'
  renderProbe()
}

function renderProbe() {
  const box = $('#cloudProbe')
  const d = S.cloudProbe
  if (!d) { box.innerHTML = ''; return }
  if (d.kind === 'err') {
    box.innerHTML = `<tr><td class="t" style="color:var(--err)">${esc(d.msg)}</td></tr>`
    return
  }
  if (d.kind === 'test') {
    const line = (nm, r) => {
      const bad = !r || r.error
      return `<tr><td class="t" style="width:180px">${nm}</td>`
        + `<td class="num" style="width:52px;color:var(--${bad ? 'err' : 'ok'})">${bad ? '失败' : '通'}</td>`
        + `<td class="t" style="color:var(--ink3)">${esc(bad ? (r ? r.error : '无响应') : JSON.stringify(r).slice(0, 140))}</td></tr>`
    }
    const h = d.health || {}
    const step = x => `<tr><td class="t" style="width:180px">读链路 · ${esc(x.action)}</td>`
      + `<td class="num" style="width:52px;color:var(--${x.ok ? 'ok' : 'err'})">${x.ok ? '通' : '失败'}</td>`
      + `<td class="t" style="color:var(--ink3)">${esc(x.ok ? (x.detail || '') : (x.error || ''))}</td></tr>`
    box.innerHTML = `<tr><th>云函数</th><th style="width:52px">状态</th><th>返回</th></tr>${
      line('function-jy-content', d.content)}${line('function-jy-upload', d.upload)
    }${(h.steps || []).map(step).join('')
    }${h.hint ? `<tr><td class="t" colspan="3" style="color:var(--warn)">${esc(h.hint)}</td></tr>` : ''}`
    return
  }
  if (d.kind === 'init') {
    const r = d.res || {}
    const col = r.collections || {}
    box.innerHTML = `<tr><th>初始化</th><th>结果</th></tr>${
      _crow('新建集合', esc((col.created || []).join('、') || '无（都已存在）'))
    }${_crow('已存在', esc((col.skipped || []).join('、')))
    }${_crow('meta 文档', `site · dataVersion=${(r.meta || {}).dataVersion}`)}`
    return
  }
  if (d.kind === 'status') {
    const R = (d.res || {}).remote || {}
    const L = (d.res || {}).local || {}
    const RC = R.counts || {}
    const row = (k, rv, lv) => {
      const gap = Number(rv) - Number(lv || 0)
      const color = gap === 0 ? 'var(--ok)' : gap > 0 ? 'var(--warn)' : 'var(--err)'
      return `<tr><td class="t">${k}</td><td class="num">${rv}</td><td class="num">${lv == null ? '-' : lv}</td>`
        + `<td class="num" style="color:${color}">${gap === 0 ? '一致' : (gap > 0 ? `线上多 ${gap}` : `线上少 ${-gap}`)}</td></tr>`
    }
    const recent = (R.recent || []).slice(0, 3).map(x =>
      `v${x.dataVersion} · ${new Date(x.releasedAt).toLocaleString()} · ${esc(x.note || '')}`).join('<br>') || '—'
    box.innerHTML = `<tr><th>项目</th><th style="width:72px">线上</th><th style="width:72px">本地</th><th style="width:96px">差</th></tr>${
      row('分集', RC.episodes, L.pushed)}${row('栏目', RC.columns, L.articles)
    }${row('账号', RC.accounts, L.accounts)}${row('上架记录', RC.releases, null)
    }<tr><td class="t">dataVersion</td><td class="num">${R.dataVersion}</td><td class="num" colspan="2" style="color:var(--ink3)">${esc(recent)}</td></tr>`
    + `<tr><td class="t">听文章入口</td><td class="num" colspan="3" style="color:${(R.settings || {}).showAudio ? 'var(--ok)' : 'var(--ink3)'}">${(R.settings || {}).showAudio ? '开（jy_meta → settings.showAudio = true）' : '关（要开就在 jy_meta 的 settings 里把 showAudio 改成 true）'}</td></tr>`
    + `<tr><td class="t">待推 / 待同步</td><td class="num" colspan="3">${L.pending || 0} 集成文未推，${(d.res || {}).diff ? Math.max(0, -(d.res.diff.episodes || 0)) : 0} 集本地已推但线上没有</td></tr>`
    return
  }
  if (d.kind === 'result') {
    box.innerHTML = `<tr><td class="t" style="color:var(--ink3)">${esc(d.label)}</td><td class="t">${esc(JSON.stringify(d.res).slice(0, 400))}</td></tr>`
  }
}

function cloudBody() {
  return {
    all: true,
    limit: Number($('#pubLimit').value || 0),
    force: $('#pubForce').checked,
    dry_run: $('#pubDry').checked,
    note: $('#pubNote').value.trim(),
  }
}

/* ---------- 轮询 ---------- */
async function refresh(full = true) {
  const ov = await api('/api/overview')
  S.accounts = ov.accounts; S.columns = ov.columns; S.jobs = ov.jobs; S.overview = ov
  renderChips(ov.stats); renderState(ov); renderCookie(ov); renderAccounts(); renderJobs()
  if (S.view === 'audio')
    renderAudio(ov)
  if (S.view === 'output')
    renderCloud(ov)
  if (full && S.view === 'queue')
    loadQueue()
  if (full && S.view === 'columns')
    renderColumns()
  const running = (ov.jobs || []).some(j => j.status === 'running') || (ov.process || {}).running
  clearTimeout(refresh._t)
  refresh._t = setTimeout(() => refresh(false).catch(() => {}), running ? 2500 : 9000)
}

/* ---------- 事件绑定 ---------- */
$('#btnReload').onclick = () => refresh()
$('#btnStop').onclick = () => guard(async () => {
  await api('/api/stop', { method: 'POST' })
  toast('已发出停止请求：当前这一条做完就收工', 'ok')
})
$('#btnScanAll').onclick = () => startScan([], '全部启用账号')
$('#btnCheck').onclick = () => guard(async () => {
  $('#btnCheck').disabled = true
  try {
    const r = await api('/api/check', { method: 'POST', body: { sec_user_id: $('#fAcct').value || '' } })
    S.cookieState = { checked: true, ...r }
    renderCookie(S.overview)
    toast(r.message || (r.ok ? '登录态正常' : '登录态异常'), r.ok ? 'ok' : 'err')
  }
  finally { $('#btnCheck').disabled = false }
})
$('#btnLogin').onclick = () => guard(async () => {
  const r = await api('/api/login', { method: 'POST', body: { sec_user_id: $('#fAcct').value || '' } })
  toast(r.message, 'ok'); refresh()
})
$('#btnBrowserReset').onclick = () => guard(async () => {
  $('#btnBrowserReset').disabled = true
  try {
    const r = await api('/api/browser/reset', { method: 'POST', body: {} })
    toast(r.message, 'ok'); S.cookieState = null; refresh()
  }
  finally { $('#btnBrowserReset').disabled = false }
})
$('#btnCookieSave').onclick = () => guard(async () => {
  const raw = $('#cookieText').value.trim()
  if (!raw)
    throw new Error('请先粘贴 Cookie')
  const r = await api('/api/cookie', { method: 'POST', body: { cookie: raw, inject: $('#cookieInject').checked } })
  toast(r.message || 'Cookie 已保存', 'ok')
  $('#cookieText').value = ''
  S.cookieState = null; refresh()
})
$('#btnFilter').onclick = () => { S.offset = 0; loadQueue() };
['fAcct', 'fCol', 'fStage', 'fOrder'].forEach((id) => {
  $(`#${id}`).onchange = () => { S.offset = 0; loadQueue().catch(e => toast(e.message, 'err')) }
})
$('#fQ').onkeydown = (e) => { if (e.key === 'Enter') { S.offset = 0; loadQueue() } }
$('#btnPrev').onclick = () => { S.offset = Math.max(0, S.offset - S.limit); loadQueue() }
$('#btnNext').onclick = () => { S.offset += S.limit; loadQueue() }
$('#btnSelClear').onclick = () => { S.selected.clear(); renderQueue() }
$('#btnSelPending').onclick = () => {
  S.videos.filter(v => !['article', 'published', 'skipped'].includes(v.stage))
    .forEach(v => S.selected.add(v.aweme_id))
  renderQueue()
}
$('#ckAll').onchange = (e) => {
  S.videos.forEach((v) => {
    if (e.target.checked)
      S.selected.add(v.aweme_id); else S.selected.delete(v.aweme_id)
  })
  renderQueue()
}
$('#btnProcessAll').onclick = () => {
  const n = Number($('#pendingN').textContent) || 0
  if (!n)
    return toast('没有待处理的作品', 'ok')
  if (!confirm(`将处理全部 ${n} 个未成文作品（${S.steps.join(' → ')}），预计耗时较长，可随时点“停止”`))
    return
  guard(async () => {
    const r = await api('/api/process', { method: 'POST', body: { all: true, steps: S.steps } })
    toast(`已排入队列：${r.queued || n} 条`, 'ok'); refresh()
  })
}
$('#btnSteps').onclick = () => {
  const STEPS = [['audio', '下载音频'], ['transcript', '语音转写'], ['article', '生成文章']]
  const cur = new Set(S.steps)
  const txt = prompt('勾选要跑的步骤（逗号分隔：audio / transcript / article）', [...cur].join(','))
  if (txt === null)
    return
  const picked = txt.split(/[,，\s]+/).filter(x => STEPS.some(s => s[0] === x))
  if (!picked.length)
    return toast('没选任何步骤', 'err')
  S.steps = picked
  $('#btnSteps').textContent = picked.map(p => ({ audio: '音频', transcript: '转写', article: '文章' }[p])).join('+')
}
$('#btnProcess').onclick = () => {
  if (S.selected.size)
    return startProcess([...S.selected])
  guard(async () => {
    const r = await api('/api/process', {
      method: 'POST',
      body: { sec_user_id: $('#fAcct').value || '', stage: 'pending', steps: S.steps, limit: Number(prompt('本次处理多少条？（0=按筛选全部）', '20') || 0) },
    })
    toast(`已按筛选排入队列：${r.queued || '全部'} 条`, 'ok'); refresh()
  })
}
$('#dClose').onclick = () => { $('#drawer').classList.remove('on'); $('#audio').pause() }
$('#dPlay').onclick = () => {
  const au = $('#audio')
  if (!au.getAttribute('src'))
    return toast('这条还没有音频文件', 'err')
  $('#dPlayer').style.display = ''
  au.play()
}
$('#dPub').onclick = () => guard(async () => {
  const r = await api('/api/cloud/publish', { method: 'POST', body: { ids: [S.detail.aweme_id], note: '单集上架' } })
  toast(`已提交云端上架（集 ${r.job_id.slice(-6)}）：传文件 → 写库 → 版本 +1`, 'ok'); refresh()
})
$('#btnBuild').onclick = () => guard(async () => {
  const sec = $('#cbAcct').value
  await api('/api/columns/build', { method: 'POST', body: { sec_user_id: sec, all: !sec, use_ai: $('#cbAI').checked } })
  toast(sec ? '开始为所选账号分栏目' : '开始为全部账号分栏目（会调用模型，进度看左下任务）', 'ok')
  refresh()
})
$('#btnColNew').onclick = () => guard(async () => {
  const sec = $('#cbAcct').value || (S.accounts[0] || {}).sec_user_id
  if (!sec)
    throw new Error('请先添加账号')
  const name = prompt('新栏目名（例如：大风歌）')
  if (!name)
    return
  await api('/api/columns', { method: 'POST', body: { sec_user_id: sec, name } })
  toast('已创建（锁定状态，AI 不会覆盖）', 'ok'); refresh()
})
/* 云端：配置 */
$('#btnCloudSave').onclick = () => guard(async () => {
  const r = await api('/api/cloud/config', {
    method: 'POST',
    body: {
      base_url: $('#cfBase').value.trim(),
      storage_host: $('#cfHost').value.trim(),
      prefix: $('#cfPrefix').value.trim(),
      token: $('#cfToken').value.trim(),
    },
  })
  S.cloud = r; S.cloudProbe = null; renderCloud({ cloud: r })
  toast('云端配置已保存，无需重启即刻生效', 'ok')
})

$('#btnCloudTest').onclick = () => guard(async () => {
  const b = $('#btnCloudTest'); b.disabled = true
  try {
    const r = await api('/api/cloud/test', { method: 'POST' })
    const bad = [r.content, r.upload].filter(x => !x || x.error)
    const h = r.health || {}
    const badSteps = (h.steps || []).filter(x => !x.ok)
    let summary
    if (bad.length) {
      summary = `${bad.length} 个云函数不可用：未部署 / 未开 URL 化 / 令牌不符`
    }
    else if (!h.ok) {
      summary = `云函数通，但线上读链路 ${badSteps.length} 处失败：${
        badSteps.map(x => x.action).join('、')}`
    }
    else {
      summary = `content / upload 都通，线上读链路 ${(h.steps || []).length} 步全通过`
    }
    S.cloudProbe = { kind: 'test', content: r.content, upload: r.upload, health: h, ok: bad.length === 0 && h.ok !== false, summary }
    refresh(false).catch(() => {})
    toast(S.cloudProbe.ok ? '云函数与线上读链路全通，可以上架' : summary, S.cloudProbe.ok ? 'ok' : 'err')
  }
  finally { setTimeout(() => { b.disabled = false }, 500) }
})

$('#btnCloudInit').onclick = () => guard(async () => {
  const b = $('#btnCloudInit'); b.disabled = true
  try {
    const r = await api('/api/cloud/init', { method: 'POST', body: { app: null } })
    S.cloudProbe = { kind: 'init', res: r }
    toast(`集合就绪：新建 ${(r.collections || {}).created.length} 个，已存在 ${(r.collections || {}).skipped.length} 个`, 'ok')
    refresh()
  }
  finally { setTimeout(() => { b.disabled = false }, 500) }
})

$('#btnCloudStatus').onclick = () => guard(async () => {
  const b = $('#btnCloudStatus'); b.disabled = true
  try {
    const r = await api('/api/cloud/status', { method: 'POST' })
    S.cloudProbe = { kind: 'status', res: r }
    renderCloud({ cloud: (r.local || {}).cloud || S.cloud })
    toast(`线上 v${(r.remote || {}).dataVersion} · 分集 ${(r.remote || {}).counts.episodes} · 本地已推 ${r.local.pushed}`, 'ok')
  }
  catch (e) {
    S.cloudProbe = { kind: 'err', msg: `比对线上失败：${e.message}` }; refresh()
    throw e
  }
  finally { setTimeout(() => { b.disabled = false }, 500) }
})

/* 云端：上架 */
async function doPublish(extra, label) {
  const body = Object.assign(cloudBody(), extra || {})
  const r = await api('/api/cloud/publish', { method: 'POST', body })
  toast(`${label}已提交：${r.scope} · 进度看左下任务`, 'ok')
  S.cloudProbe = null; refresh()
}

$('#btnPublish').onclick = () => guard(async () => {
  const sec = $('#pubAcct').value; const col = $('#pubCol').value
  if (!sec && !col) {
    if (!confirm('未选账号/栏目，按「全部待上架」处理？'))
      return
    return doPublish({ all: true }, '全量上架')
  }
  await doPublish({ sec_user_id: sec, column_id: col }, '该范围上架')
})

$('#btnPublishAll').onclick = () => guard(async () => {
  if (!confirm(`把当前所有「已成文」条目全部推上云？（${(S.cloud || {}).pending || '?'} 集）`))
    return
  await doPublish({ all: true, sec_user_id: '', column_id: '' }, '全量上架')
})

$('#btnCloudTexts').onclick = () => guard(async () => {
  const sec = $('#pubAcct').value; const col = $('#pubCol').value
  if (!sec && !col && !confirm('未选账号/栏目，按「全部已上线」刷新正文？'))
    return
  const body = Object.assign(cloudBody(), { sec_user_id: sec, column_id: col })
  const r = await api('/api/cloud/texts', { method: 'POST', body })
  toast(`正文刷新已提交：${r.scope} · 进度看左下任务`, 'ok')
  S.cloudProbe = null; refresh()
})

$('#btnMetaSync').onclick = () => guard(async () => {
  const r = await api('/api/cloud/sync', { method: 'POST', body: { note: $('#pubNote').value.trim() || '只同步元数据' } })
  toast('元数据同步任务已开始（不重传音频和正文）', 'ok'); refresh()
})

$('#btnCloudCovers').onclick = () => guard(async () => {
  const r = await api('/api/cloud/covers', {
    method: 'POST',
    body: { force: $('#coverForce').checked, dry_run: $('#coverDry').checked },
  })
  toast('封面转存任务已开始：一个合集一张，进度看左下任务', 'ok')
  S.cloudProbe = null; refresh()
})

$('#btnOffline').onclick = () => guard(async () => {
  const ids = [...S.selected]
  const sec = $('#pubAcct').value; const col = $('#pubCol').value; const purge = $('#purgeFiles').checked
  let body
  if (ids.length) {
    if (!confirm(`下架 ${ids.length} 集？线上会立刻看不到了。`))
      return
    body = { ids, purge_files: purge }
  }
  else if (col || sec) {
    if (!confirm(`没有勾选条目，按${col ? '选中栏目' : '选中账号'}整体下架？线上会立刻看不到了。`))
      return
    body = { column_id: col, sec_user_id: sec, purge_files: purge }
  }
  else {
    throw new Error('先在「作品队列」勾选条目，或在上方选好栏目/账号再点下架')
  }
  const r = await api('/api/cloud/offline', { method: 'POST', body })
  toast(`已下架 ${r.removed} 集，删除文件 ${r.filesRemoved || 0} 个`, 'ok')
  S.cloudProbe = null; S.selected.clear(); refresh()
})

$('#btnRestore').onclick = () => guard(async () => {
  const ids = [...S.selected]
  const sec = $('#pubAcct').value; const col = $('#pubCol').value
  let body
  if (ids.length) {
    body = { ids }
  }
  else if (col || sec) {
    if (!confirm(`没有勾选条目，按${col ? '选中栏目' : '选中账号'}整体重新上架？`))
      return
    body = { column_id: col, sec_user_id: sec }
  }
  else {
    throw new Error('先在「作品队列」勾选要恢复的条目，或在上方选好栏目/账号')
  }
  const r = await api('/api/cloud/restore', { method: 'POST', body })
  toast(`已重新上架 ${r.restored} 集${r.needsUpload && r.needsUpload.length ? `，其中 ${r.needsUpload.length} 集文件被删过，需再点一次上架重传` : ''}`, 'ok')
  S.cloudProbe = null; S.selected.clear(); refresh()
})

$('#btnReconcile').onclick = () => guard(async () => {
  const r = await api('/api/cloud/reconcile', { method: 'POST' })
  if (r.skipped)
    throw new Error(r.skipped)
  toast(`线上 ${r.remote} 集：补记下架 ${r.offline}、补记上架 ${r.online}`, 'ok')
  S.cloudProbe = null; refresh()
})

$('#btnRetract').onclick = () => guard(async () => {
  const ids = [...S.selected]
  if (!ids.length)
    throw new Error('先在「作品队列」勾选条目')
  const r = await api('/api/cloud/retract', { method: 'POST', body: { ids } })
  toast(`已撤回 ${r.retracted} 集的云端地址，下次上架重推`, 'ok')
  S.selected.clear(); refresh()
})

$('#btnExport').onclick = () => guard(async () => {
  const r = await api('/api/export', { method: 'POST', body: {} })
  toast(`离线 json：articles ${r.articles} / playlist ${r.playlist} / columns ${r.columns}`, 'ok')
})
$('#btnExportDraft').onclick = () => guard(async () => {
  const r = await api('/api/export', { method: 'POST', body: { draft: true } })
  toast(`已导出预览数据（含未上架）：${r.articles} 条`, 'ok')
})
$('#btnLogReload').onclick = () => loadLog().catch(() => {})
$('#btnAudioSave').onclick = () => guard(async () => {
  $('#btnAudioSave').disabled = true
  try {
    const r = await api('/api/audio/profile', { method: 'POST', body: { profile: $('#audioProfile').value } })
    toast(r.message, 'ok'); refresh()
  }
  finally { $('#btnAudioSave').disabled = false }
})
$('#btnAudioEstimate').onclick = () => guard(async () => {
  $('#btnAudioEstimate').disabled = true
  try {
    const r = await api('/api/audio/compact', {
      method: 'POST',
      body: { dry_run: true, profile: $('#audioProfile').value, include_published: $('#audioPub').checked },
    })
    toast(`待转 ${r.count} 集：${r.avg_before_mb} MB/集 → 约 ${r.avg_after_mb} MB/集，预计省 ${r.estimate} GB`, 'ok')
  }
  finally { $('#btnAudioEstimate').disabled = false }
})
$('#btnAudioCompact').onclick = () => guard(async () => {
  $('#btnAudioCompact').disabled = true
  try {
    const r = await api('/api/audio/compact', {
      method: 'POST',
      body: { profile: $('#audioProfile').value, keep_source: $('#audioKeep').checked, include_published: $('#audioPub').checked, workers: 4 },
    })
    toast('瘦身任务已开始，进度看「任务」列表', 'ok')
    setTimeout(() => refresh(), 4000)
  }
  finally { setTimeout(() => { $('#btnAudioCompact').disabled = false }, 3000) }
})

$$('.tabs button').forEach((b) => { b.onclick = () => showView(b.dataset.view) })
$('#btnAddOpen').onclick = () => $('#mAdd').classList.add('on')
$$('#mAdd [data-close],#mAdd .ft .btn').forEach((b) => {
  if (b.id === 'aSave')
    return
  b.onclick = () => $('#mAdd').classList.remove('on')
})
$('#mAdd').addEventListener('click', (e) => {
  if (e.target.id === 'mAdd')
    $('#mAdd').classList.remove('on')
})
$('#aSave').onclick = () => guard(async () => {
  const raw = $('#aRaw').value.trim()
  if (!raw)
    throw new Error('请粘贴主页链接')
  const acc = await api('/api/accounts', {
    method: 'POST',
    body: { raw, name: $('#aName').value.trim(), style: $('#aStyle').value.trim(), max_items: Number($('#aMax').value) || 0, probe: $('#aProbe').checked },
  })
  $('#mAdd').classList.remove('on')
  $('#aRaw').value = $('#aName').value = $('#aStyle').value = ''
  toast(`已添加：${acc.name || acc.sec_user_id}`, 'ok')
  if (acc.probe) {
    S.probeResult = acc.probe
    toast(acc.probe.ok
      ? `试探成功：${acc.probe.nickname || ''}，主页 ${acc.probe.aweme_count} 条，${(acc.probe.mixes || []).length} 个可见合集`
      : `试探失败：${acc.probe.message}`, acc.probe.ok ? 'ok' : 'err')
  }
  refresh()
})
$('#btnHelp').onclick = () => $('#mAdd').classList.remove('on')

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { $('#drawer').classList.remove('on'); $('#mAdd').classList.remove('on') }
  if (e.key === 'r' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); refresh() }
})

refresh().catch(e => toast(`面板后端没连上：${e.message}`, 'err'))
